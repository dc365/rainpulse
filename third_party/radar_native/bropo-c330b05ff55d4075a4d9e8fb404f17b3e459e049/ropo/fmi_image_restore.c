/**

    Copyright 2001 - 2010  Markus Peura, 
    Finnish Meteorological Institute (First.Last@fmi.fi)


    This file is part of bRopo.

    bRopo is free software: you can redistribute it and/or modify
    it under the terms of the GNU Lesser Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    bRopo is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Lesser Public License for more details.

    You should have received a copy of the GNU Lesser Public License
    along with bRopo.  If not, see <http://www.gnu.org/licenses/>. */

#include "fmi_image_restore.h"
#include "fmi_image_filter.h"
#include "fmi_image_histogram.h"


void mark_image(FmiImage *target,FmiImage *prob,Byte threshold,Byte marker){ 
  register int i;
  check_image_properties(target,prob);

  for (i=0;i<prob->volume;i++)
    if (prob->array[i]>=threshold)
      target->array[i]=marker;
}


/* simple */
void restore_image(FmiImage *source, FmiImage *target, FmiImage *prob, Byte threshold){
  register int i;
  canonize_image(source,prob);
  canonize_image(source,target);

  for (i=0;i<prob->volume;i++) {
    if (prob->array[i ]>= threshold) {
      target->array[i] = 0;
      target->original[i] = target->original_undetect;
    } else {
      target->array[i]=source->array[i];
      target->original[i] = source->original[i];
    }
  }
}

void restore_image_neg(FmiImage *source,FmiImage *target,FmiImage *prob,Byte threshold){ 
  register int i;
  canonize_image(source,prob);
  canonize_image(source,target);

  for (i=0;i<prob->volume;i++)
    if (prob->array[i]<threshold)
      target->array[i]=0;
    else
      target->array[i]=source->array[i];
}

static double calculate_original_mean(FmiImage* source, int x, int y, int hrad, int vrad)
{
  int h,v;
  double sum = 0.0;
  int nhits = 0;
  for (h = x - hrad; h <= x + hrad; h++) {
    for (v = y - vrad; v <= y + vrad; v++) {
      if (h >= 0 && h < source->width && v >= 0 && v < source->height) {
        double val = get_pixel_orig(source, h, v, 0);
        if (val != source->original_undetect) {
          sum += val;
          nhits++;
        }
      }
    }
  }
  if (nhits > 0) {
    return (double)(sum / (double)nhits);
  }
  return source->original_undetect;
}

static void process_original_mean(FmiImage* source, FmiImage* target, int hrad, int vrad)
{
  int x, y;
  for (x = 0; x < source->width; x++) {
    for (y = 0; y < source->height; y++) {
      put_pixel_orig(target, x, y, 0, calculate_original_mean(source, x, y, hrad, vrad));
    }
  }
}

/* other */
void restore_image2(FmiImage *source,FmiImage *target,FmiImage *prob,Byte threshold){ 
  register int i;
  FmiImage median;
  FmiImage original_mean;
  init_new_image(&median);
  init_new_image(&original_mean);

  canonize_image(source,prob);
  canonize_image(source,target);
  canonize_image(source,&median);
  canonize_image(source,&original_mean);

  /* ERASE ANOMALIES (to black) */
  for (i=0; i < prob->volume; i++) {
    if (prob->array[i] >= threshold) {
      target->array[i] = 0;
      target->original[i] = target->original_undetect;
    } else {
      target->array[i] = source->array[i];
      target->original[i] = source->original[i];
    }
  }

  /* CALCULATE ME(DI)AN OF NONZERO PIXELS */
  pipeline_process(target, &median, 2, 2, histogram_mean_nonzero); /* Affects the 8-bit version */

  process_original_mean(target, &original_mean, 2, 2); /* And the original data */

  /* REPLACE ANOMALOUS PIXELS WITH THAT NEIGHBORHOOD ME(DI)AN */
  for (i = 0; i < prob->volume; i++){
    if (prob->array[i] >= threshold) {
      target->array[i] = median.array[i];
      target->original[i] = original_mean.original[i];
    }
  }

  reset_image(&median);
  reset_image(&original_mean);
}

