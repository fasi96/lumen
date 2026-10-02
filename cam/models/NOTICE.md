# Model files

These two models are from Google's [MediaPipe](https://ai.google.dev/edge/mediapipe), used unchanged by the face bubble:

| File | What it does | Source |
|---|---|---|
| `selfie_segmenter.tflite` | Finds the person in the camera picture (the bubble's outline) | MediaPipe Image Segmenter, "Selfie segmentation" model |
| `blaze_face_short_range.tflite` | Finds your face for *Center me* | MediaPipe Face Detector, "BlazeFace (short-range)" model |

They are distributed under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0) by Google LLC; see the [MediaPipe model cards](https://ai.google.dev/edge/mediapipe/solutions/vision/image_segmenter#models) for details. The rest of Lumen is under the MIT licence in the repository root.
