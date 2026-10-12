# Studio Pro

A desktop video editor for talks and podcasts (Windows; Mac and Linux too), laid out like DaVinci Resolve.
Everything runs on your own computer; your videos are never uploaded.

- **Sync:** a separate recorder is lined up with the camera to the millisecond (clock drift corrected) and checked
  word by word. If the recorder started before the camera, the whole recording is kept and the missing picture is
  filled automatically.
- **Sound and words:**
  - AI voice cleaning (DeepFilterNet) and broadcast loudness.
  - Background music that dips under the voice.
  - Voice level per clip.
  - Transcription on this computer (Whisper), and editing by deleting words.
- **Picture:**
  - Framing that follows the face, or your own framing box (e.g. leave out the lower body).
  - Background removal or replacement (blur, colour, your picture).
  - Colour wheels, looks and LUTs; titles and lower thirds; dissolves at cuts; slow zoom; covers.
- **Delivery:**
  - Reels with word-by-word captions, and a YouTube package with chapters and thumbnails.
  - Extra copies for WhatsApp and the square feed.
  - FCPXML and EDL timelines for other editors.
- **Quality check** after every render: lip sync at 3 points, loudness, length, picture, caption readability.

See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) for plain step-by-step instructions.

## Install (Windows)

1. Open **Releases** on this page (right-hand side) and download `Studio-Pro-Setup-….exe`.
2. Double-click it and follow the steps. If Windows says “Windows protected your PC”, click **More info → Run anyway**
   (the app is not code-signed yet).
3. Open **Studio Pro** from the Desktop or Start menu.

## Licence

Free software under the GNU GPL v3. It includes FFmpeg (GPL build), DeepFilterNet (MIT/Apache-2.0), faster-whisper and
Whisper models (MIT), Silero VAD (MIT), OpenCV YuNet (Apache-2.0), Robust Video Matting (GPL-3.0), DejaVu fonts and
Electron (MIT).
