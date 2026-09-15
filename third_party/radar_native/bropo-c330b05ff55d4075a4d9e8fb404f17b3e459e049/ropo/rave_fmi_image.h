/* --------------------------------------------------------------------
Copyright (C) 2011 Swedish Meteorological and Hydrological Institute, SMHI,

This file is part of bRopo.

bRopo is free software: you can redistribute it and/or modify
it under the terms of the GNU Lesser General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

bRopo is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License
along with bRopo.  If not, see <http://www.gnu.org/licenses/>.
------------------------------------------------------------------------*/

/**
 * rave object wrapper for the fmi_image.
 * This object support \ref #RAVE_OBJECT_CLONE.
 * @file
 * @author Anders Henja (Swedish Meteorological and Hydrological Institute, SMHI)
 * @date 2011-08-31
 */
#ifndef RAVE_FMI_IMAGE_H
#define RAVE_FMI_IMAGE_H
#include "fmi_image.h"
#include "rave_object.h"
#include "polarvolume.h"
#include "polarscan.h"
#include "rave_field.h"

/**
 * Defines a RaveFmiImage
 */
typedef struct _RaveFmiImage_t RaveFmiImage_t;

/**
 * Type definition to use when creating a rave object.
 */
extern RaveCoreObjectType RaveFmiImage_TYPE;

/**
 * Initializes the rave image with the specific width and height.
 * @see \ref RaveFmiImage_new for short cut version
 * @param[in] self - self
 * @param[in] width - width
 * @param[in] height - height
 * @return 1 on success otherwise 0
 */
int RaveFmiImage_initialize(RaveFmiImage_t* self, int width, int height);

/**
 * Fills the image with the provided value.
 * @param[in] self - self
 * @param[in] v - the pixel value to set for all pixels
 */
void RaveFmiImage_fill(RaveFmiImage_t* self, unsigned char v);

/**
 * Fills the original image with the provided value.
 * @param[in] self - self
 * @param[in] v - the pixel value to set for all pixels
 */
void RaveFmiImage_fillOriginal(RaveFmiImage_t* self, double v);

/**
 * Returns the internal FmiImage.
 * @param[in] self - self
 * @return the internal fmi image (NOTE, THIS IS AN INTERNAL POINTER SO DO NOT RELEASE)
 */
FmiImage* RaveFmiImage_getImage(RaveFmiImage_t* self);

/**
 * Creates a polar scan from a fmi image
 * @param[in] self - self
 * @param[in] quantity - the quantity to be set for the parameters (may be NULL)
 * @param[in] datatype - how the data field should be defined. 0 = same as when created, 1 = force unsigned char and use data from 8 bit array, 2 = force unsigned char but use data from original data array
 * @return a polar volume on success otherwise NULL
 */
PolarScan_t* RaveFmiImage_toPolarScan(RaveFmiImage_t* self, const char* quantity, int datatype);

/**
 * Creates a rave field from a fmi image
 * @param[in] self - self
 * @param[in] datatype - how the data field should be defined. 0 = same as when created, 1 = force unsigned char and use data from 8 bit array, 2 = force unsigned char but use data from original data array
 * @return a polar volume on success otherwise NULL
 */
RaveField_t* RaveFmiImage_toRaveField(RaveFmiImage_t* self, int datatype);

/**
 * Adds a rave attribute to the image.
 * @param[in] self - self
 * @param[in] attribute - the attribute
 * @return 1 on success otherwise 0
 */
int RaveFmiImage_addAttribute(RaveFmiImage_t* self,  RaveAttribute_t* attribute);

/**
 * Returns the rave attribute that is named accordingly.
 * @param[in] self - self
 * @param[in] name - the name of the attribute
 * @returns the attribute if found otherwise NULL
 */
RaveAttribute_t* RaveFmiImage_getAttribute(RaveFmiImage_t* self, const char* name);

/**
 * Returns a list of attribute names. Release with \@ref #RaveList_freeAndDestroy.
 * @param[in] self - self
 * @returns a list of attribute names
 */
RaveList_t* RaveFmiImage_getAttributeNames(RaveFmiImage_t* self);

/**
 * Sets the gain for the data in the image.
 * Data stored to actual value is retrieved by offset + value * gain.
 * @param[in] self - self
 * @param[in] gain - the gain
 */
void RaveFmiImage_setGain(RaveFmiImage_t* self, double gain);

/**
 * Returns the gain for the data in the image
 * Data stored to actual value is retrieved by offset + value * gain.
 * @param[in] self - self
 * @return the gain
 */
double RaveFmiImage_getGain(RaveFmiImage_t* self);

/**
 * Sets the gain for the data in the image
 * Data stored to actual value is retrieved by offset + value * gain.
 * @param[in] self - self
 * @param[in] offset - the offset
 */
void RaveFmiImage_setOffset(RaveFmiImage_t* self, double offset);

/**
 * Returns the offset for the data in the image.
 * Data stored to actual value is retrieved by offset + value * gain.
 * @param[in] self - self
 * @return the offset
 */
double RaveFmiImage_getOffset(RaveFmiImage_t* self);

/**
 * Sets the nodata for the data in the image.
 * @param[in] self - self
 * @param[in] v - the nodata value
 */
void RaveFmiImage_setNodata(RaveFmiImage_t* self, double v);

/**
 * Returns the nodata for the data in the image
 * @param[in] self - self
 * @return the nodata value
 */
double RaveFmiImage_getNodata(RaveFmiImage_t* self);

/**
 * Sets the undetect for the data in the image
 * @param[in] self - self
 * @param[in] v - the undetect value
 */
void RaveFmiImage_setUndetect(RaveFmiImage_t* self, double v);

/**
 * Returns the undetect for the data in the image.
 * @param[in] self - self
 * @return the undetect value
 */
double RaveFmiImage_getUndetect(RaveFmiImage_t* self);

/**
 * Sets the original (when converted from rave object) gain for the data in the image.
 * Data stored to actual value is retrieved by offset + value * gain.
 * @param[in] self - self
 * @param[in] gain - the gain
 */
void RaveFmiImage_setOriginalGain(RaveFmiImage_t* self, double gain);

/**
 * Returns the original (when converted from rave object) gain for the data in the image
 * Data stored to actual value is retrieved by offset + value * gain.
 * @param[in] self - self
 * @return the gain
 */
double RaveFmiImage_getOriginalGain(RaveFmiImage_t* self);

/**
 * Sets the original (when converted from rave object) gain for the data in the image
 * Data stored to actual value is retrieved by offset + value * gain.
 * @param[in] self - self
 * @param[in] offset - the offset
 */
void RaveFmiImage_setOriginalOffset(RaveFmiImage_t* self, double offset);

/**
 * Returns the original (when converted from rave object) offset for the data in the image.
 * Data stored to actual value is retrieved by offset + value * gain.
 * @param[in] self - self
 * @return the offset
 */
double RaveFmiImage_getOriginalOffset(RaveFmiImage_t* self);

/**
 * Sets the original (when converted from rave object) nodata for the data in the image.
 * @param[in] self - self
 * @param[in] v - the nodata value
 */
void RaveFmiImage_setOriginalNodata(RaveFmiImage_t* self, double v);

/**
 * Returns the original (when converted from rave object) nodata for the data in the image
 * @param[in] self - self
 * @return the nodata value
 */
double RaveFmiImage_getOriginalNodata(RaveFmiImage_t* self);

/**
 * Sets the original (when converted from rave object)undetect for the data in the image
 * @param[in] self - self
 * @param[in] v - the undetect value
 */
void RaveFmiImage_setOriginalUndetect(RaveFmiImage_t* self, double v);

/**
 * Returns the original (when converted from rave object)undetect for the data in the image.
 * @param[in] self - self
 * @return the undetect value
 */
double RaveFmiImage_getOriginalUndetect(RaveFmiImage_t* self);

/**
 * Creates a rave fmi image with specified dimension
 * @param[in] width - the width
 * @param[in] height - the height
 * @return the fmi image on success otherwise NULL
 */
RaveFmiImage_t* RaveFmiImage_new(int width, int height);

/**
 * Creates a fmi image from a specific scan in a polar volume
 * @param[in] volume - the polar volume
 * @param[in] scannr - the polar scan in the volume
 * @param[in] quantity - the quantity to use for building the fmi image (NULL == default parameter)
 * @return the object on success otherwise NULL
 */
RaveFmiImage_t* RaveFmiImage_fromPolarVolume(PolarVolume_t* volume, int scannr, const char* quantity);

/**
 * Creates a fmi image from a polar scan
 * @param[in] scan - the polar scan
 * @param[in] quantity - the quantity to use for building the fmi image (NULL == default parameter)
 * @return the object on success otherwise NULL
 */
RaveFmiImage_t* RaveFmiImage_fromPolarScan(PolarScan_t* scan, const char* quantity);

/**
 * Creates a fmi image from a rave field
 * @param[in] field - the rave field
 * @return the object on success otherwise NULL
 */
RaveFmiImage_t* RaveFmiImage_fromRaveField(RaveField_t* field);

/**
 * Creates a fmi image from a rave object. Currently supported object types
 * are. PolarVolume_t, PolarScan_t and RaveField_t. If it is a polar volume
 * scan 0 is assumed.
 * @param[in] object - the object to generate an fmi image from
 * @param[in] quantity - the specific quantity (NULL == default parameter if applicable)
 * @return the object on success otherwise NULL
 */
RaveFmiImage_t* RaveFmiImage_fromRave(RaveCoreObject* object, const char* quantity);

#endif /* RAVE_FMI_IMAGE_H */
