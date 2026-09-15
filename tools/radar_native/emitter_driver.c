/* RainPulse process adapter; calls unmodified bRopo detector cores.
 * The input contains ONLY observed, quantized DBZH. Missing gates are never
 * encoded as clear air. Classification bytes are scores, not probabilities.
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include "fmi_image.h"
#include "fmi_radar_image.h"
static int number(const char *s, long *out) {
  char *end; errno=0; *out=strtol(s,&end,10);
  return !errno && end!=s && *end=='\0';
}
int main(int argc,char **argv) {
  long nr,ng,det,threshold,length,width;
  if(argc!=9 || !number(argv[3],&nr) || !number(argv[4],&ng) ||
     !number(argv[5],&det) || !number(argv[6],&threshold) ||
     !number(argv[7],&length) || !number(argv[8],&width)) return 2;
  if(nr<3 || nr>5000 || ng<4 || ng>10000 || (det!=1 && det!=2) ||
     threshold<1 || threshold>254 || length<2 || length>254 || width<1 || width>32) return 3;
  size_t n=(size_t)nr*(size_t)ng;
  FmiImage src,dst; init_new_image(&src);init_new_image(&dst);
  src.width=(int)ng;src.height=(int)nr;src.channels=1;initialize_image(&src);
  FILE *f=fopen(argv[1],"rb");if(!f) return 4;
  int ok=fread(src.array,1,n,f)==n && fgetc(f)==EOF;fclose(f);
  if(!ok) return 5;
  for(size_t i=0;i<n;i++) {
    if(src.array[i]==0 || src.array[i]==255) return 6;
    src.original[i]=(double)src.array[i];
  }
  if(det==1) detect_emitters(&src,&dst,(int)threshold,(int)length);
  else detect_emitters2(&src,&dst,(int)threshold,(int)length,(int)width);
  f=fopen(argv[2],"wb");if(!f) return 7;
  ok=fwrite(dst.array,1,n,f)==n;if(fclose(f)) ok=0;
  reset_image(&src);reset_image(&dst);return ok?0:8;
}
