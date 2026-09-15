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
 * This object DOES NOT support \ref #RAVE_OBJECT_CLONE.
 * @file
 * @author Anders Henja (Swedish Meteorological and Hydrological Institute, SMHI)
 * @date 2011-09-02
 */
#ifndef RAVE_FMI_VOLUME_H
#define RAVE_FMI_VOLUME_H
#include "fmi_image.h"
#include "rave_object.h"
#include "polarvolume.h"
#include "polarscan.h"

/**
 * Defines a RaveFmiVolume
 */
typedef struct _RaveFmiVolume_t RaveFmiVolume_t;

/**
 * Type definition to use when creating a rave object.
 */
extern RaveCoreObjectType RaveFmiVolume_TYPE;

/**
 * Initializes the rave image with the specific number of sweeps.
 * @see \ref RaveFmiVolume_new for short cut version
 * @param[in] self - self
 * @param[in] sweepCount - number of sweeps this image should consist of > 0
 * @return 1 on success otherwise 0
 */
int RaveFmiVolume_initialize(RaveFmiVolume_t* self, int sweepCount);

/**
 * Returns the number of sweeps this image is built from
 * @param[in] self - self
 * @return number of sweeps
 */
int RaveFmiVolume_getSweepCount(RaveFmiVolume_t* self);

/**
 * Returns the internal FmiImage.
 * @param[in] self - self
 * @return the internal fmi image (NOTE, THIS IS AN INTERNAL POINTER SO DO NOT RELEASE)
 */
FmiImage* RaveFmiVolume_getImage(RaveFmiVolume_t* self);

/**
 * Returns the internal sweep pointer.
 * @param[in] self - self
 * @param[in] sweep - the sweep index
 * @return the sweep at specified index
 */
FmiImage* RaveFmiVolume_getSweep(RaveFmiVolume_t* self, int sweep);

/**
 * Creates a polar volume from a fmi image
 * @param[in] self - self
 * @param[in] quantity - the quantity to be set for the parameters (may be NULL)
 * @return a polar volume on success otherwise NULL
 */
PolarVolume_t* RaveFmiVolume_toRave(RaveFmiVolume_t* self, const char* quantity);

/**
 * Creates a rave fmi image with specified number of sweeps
 * @param[in] sweepCount - the number of sweeps > 0
 * @return the fmi image on success otherwise NULL
 */
RaveFmiVolume_t* RaveFmiVolume_new(int sweepCount);

/**
 * Creates a fmi image from a polar volume
 * @param[in] volume - the polar volume
 * @param[in] quantity - the quantity to use for building the fmi image (NULL == default parameter)
 * @return the object on success otherwise NULL
 */
RaveFmiVolume_t* RaveFmiVolume_fromPolarVolume(PolarVolume_t* volume, const char* quantity);

/**
 * Creates a fmi image from a polar scan
 * @param[in] scan - the polar scan
 * @param[in] quantity - the quantity to use for building the fmi image (NULL == default parameter)
 * @return the object on success otherwise NULL
 */
RaveFmiVolume_t* RaveFmiVolume_fromPolarScan(PolarScan_t* scan, const char* quantity);

/**
 * Creates a fmi image from a rave field
 * @param[in] field - the rave field
 * @return the object on success otherwise NULL
 */
RaveFmiVolume_t* RaveFmiVolume_fromRaveField(RaveField_t* field);

/**
 * Creates a fmi image from a rave object. Currently supported object types
 * are. PolarVolume_t, PolarScan_t and RaveField_t
 * @param[in] object - the object to generate an fmi image from
 * @param[in] quantity - the specific quantity (NULL == default parameter if applicable)
 * @return the object on success otherwise NULL
 */
RaveFmiVolume_t* RaveFmiVolume_fromRave(RaveCoreObject* object, const char* quantity);

#endif /* RAVE_FMI_VOLUME_H */
