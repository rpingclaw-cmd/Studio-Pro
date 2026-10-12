# Studio Pro — user guide (plain steps)

Studio Pro turns a recorded talk into a finished video, podcast audio, Reels and a YouTube package.
Everything happens on your own computer; nothing is uploaded.

## 1. Install

1. Download `Studio-Pro-Setup-….exe` from the **Releases** page of this project.
2. Double-click it. If Windows shows “Windows protected your PC”, click **More info → Run anyway**.
3. Open **Studio Pro** from the Desktop. It opens straight into the editor.

The first time you transcribe, remove a background or check the sync with words, Studio Pro downloads the AI model
it needs (140 MB to 1.5 GB, once). Keep the internet on for that first time.

## 2. The editor (looks and works like DaVinci Resolve)

Pages along the bottom: **Media · Edit · Color · Fairlight · Deliver**. **Guided view** (top right) shows the same
work as numbered steps. Everything is saved as you go; **Ctrl+Z** undoes any change.

Timeline tracks:

| Track | What it is |
|---|---|
| T1 | Titles and lower thirds |
| V2 | Covers: other pictures over a part, while the sound keeps playing |
| V1 | Your video (hatched where the camera was not running — see 4) |
| A1 | Voice — the camera sound, or the cleaned recorder sound |
| M1 | Background music |
| ST | Subtitles from the transcript |

Keys: Space play/pause · J K L backwards/stop/forwards · ← → one frame · I / O mark in/out · Ctrl+B cut · Delete
ripple delete · M marker (chapter) · T title · Shift+Z fit · ? all keys.

## 3. Media page: files and sync

1. **Choose video…** and **Choose separate audio…** (or drag the files onto the window).
2. **Prepare** — makes a light preview copy and reads the sound. (The first time, it measures the fastest way to read
   your video on this computer.)
3. **Find sync automatically** — lines up the recorder with the camera to the millisecond, corrects clock drift,
   and then checks the *words* at 3 points: “Words agree at 3 of 3 points ✓”. If the camera sound is too poor for
   the sound comparison, the words alone find the sync.

## 4. Keep the whole recording (audio started before the video)

If the recorder was started before the camera (or kept running after it), tick **Keep the whole recording** in the
sync step. That speech is kept; where there is no camera picture, the picture is filled automatically with
**moments where the speaker listens**, a **title card**, or **your own photo/video**. On the timeline those parts
are hatched and labelled “No camera — filled”; they play with the recorder sound.

## 5. Sound (Fairlight page)

1. Set the voice isolation strength and press **Preview 30 s before / after**.
2. Press **Clean the whole recording**. From then on the editor plays the cleaned recorder sound
   (button **Sound: recorder / camera** above the picture to compare).
3. **Background music:** Choose music… — it loops to the length of the video, fades in and out, and **dips under
   the voice** (ducking). Set its level and how much it dips; tick “Also under the Reels” if you want it there.
4. **Voice level per clip:** click a clip on the timeline → Inspector → Voice level. The final loudness is set again
   automatically (−16 LUFS for YouTube and podcasts).

## 6. Edit page: transcript, cuts, titles

- **Transcribe** runs on this computer (choose Fast / Accurate / Best). Unsure words are underlined.
- **Use my accurate transcript** lines up your exact text with the recording (word for word).
- Select words and press **Delete** (or Cut) to remove them from the video. Pauses, “um”s and missed sounds can be
  removed automatically.
- **Titles / lower thirds:** press **T** (or “T Title”) at the playhead; type the words and a second line in the
  Inspector, choose Lower third / Centre / Top and the length.
- **At every cut** (Picture panel): a close-up (hides jump cuts), a **smooth dissolve**, or a plain cut.
- **Slow zoom:** click a clip → Inspector → “Slow zoom in through this clip”.

## 7. Color page: picture, framing, background, colour

- **Shape:** YouTube 16:9 that follows the speaker, 16:9 with blurred sides, or as recorded.
- **My own framing box:** “Draw my framing box…” → drag the box over the picture (drag its corner to resize) →
  **Done**. Move it up to leave out the lower body. For one clip only: click the clip → Inspector → Framing → “My own box”.
- **Background:** keep it, **blur** it, a **plain colour**, or **your picture**. Press **Find the person** once per
  video (it looks at every frame; on a 4-core laptop roughly the length of the video), then **Show a still** to check.
- **Colour wheels:** Lift (shadows), Gamma (midtones), Gain (highlights) — drag a dot towards a colour; double-click
  to reset. **Looks:** Natural, Warm, Cool, Cinematic, Soft, Vivid, Black & white. **LUT:** use a `.cube` look file.
- **Show a still** shows exactly how the finished picture will look at the playhead.

## 8. Deliver page: the finished files

Press **Make the final files on this computer**. In the folder of your video you get:

- the edited video, full audio (MP3 + WAV), captions (SRT + VTT), chapters, the YouTube text, Reels;
- a timeline for DaVinci Resolve (FCPXML) and a cut list (EDL);
- optional extra copies: **small for WhatsApp** and **square for the Instagram / Facebook feed**;
- `Edited - quality check.txt`: lip sync measured at 3 points, loudness, length, picture, caption readability.

## Free tools inside

FFmpeg 8.1 (GPL), DeepFilterNet 0.5.6 (MIT/Apache-2.0), faster-whisper (MIT) with OpenAI Whisper models (MIT),
Silero VAD (MIT), OpenCV YuNet (Apache-2.0), Robust Video Matting (GPL-3.0), DejaVu fonts (free licence), Electron (MIT).
