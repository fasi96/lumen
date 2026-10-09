# Model files

These two models are from Google's [MediaPipe](https://ai.google.dev/edge/mediapipe), used unchanged by the face bubble:

| File | What it does | Source |
|---|---|---|
| `selfie_segmenter.tflite` | Finds the person in the camera picture (the bubble's outline) | MediaPipe Image Segmenter, "Selfie segmentation" model |
| `blaze_face_short_range.tflite` | Finds your face for *Center me* | MediaPipe Face Detector, "BlazeFace (short-range)" model |

They are distributed under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) by Google LLC; see the [MediaPipe model cards](https://ai.google.dev/edge/mediapipe/solutions/vision/image_segmenter#models) for details. The rest of Lumen is under the MIT licence in the repository root.

## Exact files

| File | sha256 | Downloaded from |
|---|---|---|
| `selfie_segmenter.tflite` | `191ac9529ae506ee0beefa6b2c945a172dab9d07d1e802a290a4e4038226658b` | https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite |
| `blaze_face_short_range.tflite` | `b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f` | https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/latest/blaze_face_short_range.tflite |

Check them with `sha256sum cam/models/*.tflite`.
