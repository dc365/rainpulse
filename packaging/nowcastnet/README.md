# NowcastNet official capsule compatibility

RainPulse does not vendor the official NowcastNet source, checkpoint or sample data in Git.
Obtain Code Ocean capsule `10.24433/CO.0832447.v1`, then stage the reviewed archive with:

```bash
scripts/stage_nowcastnet_official.sh capsule-3935105.zip runtime/nowcastnet/official-v1
```

The staging script verifies the capsule and checkpoint SHA-256 before extracting, rejects unsafe
ZIP paths and applies `official-v1-device-compat.patch` with zero fuzz. The patch contains only
device-compatibility changes:

- create coordinate grids on the input device;
- register the grid as a non-persistent buffer so it follows the model device without changing the
  official checkpoint state dictionary;
- remove hard-coded `.cuda()` calls from grid and noise creation;
- load weights with an explicit target device.

Official code is MIT licensed. Capsule data and `mrms_model.ckpt` are distributed under CC0 1.0.
Keep both license files with every staged runtime copy.

The original capsule environment uses PyTorch 1.12.1 and CUDA 11.7. The 105 test GPU has compute
capability 12.0, whose first native CUDA Toolkit support is 12.8, so RP-026 freezes a separate
compatibility runtime in the profile. This runtime is for offline evaluation only until GPU
resource, numerical and hindcast acceptance are complete.
