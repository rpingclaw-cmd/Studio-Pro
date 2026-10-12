"""Show the computer's own "Open file" window and print the chosen path (runs as a separate small process)."""
import subprocess
import sys

KINDS = {
    "video": ("Choose your video", [("Video", "*.mov *.mp4 *.m4v *.mkv *.avi *.MOV *.MP4"), ("All files", "*.*")]),
    "audio": ("Choose your separate audio recording",
              [("Audio", "*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.WAV *.MP3 *.M4A"), ("All files", "*.*")]),
    "transcript": ("Choose your transcript", [("Text", "*.txt *.docx *.srt *.vtt"), ("All files", "*.*")]),
    "cover": ("Choose a photo or video for the covered part",
              [("Photos and videos", "*.jpg *.jpeg *.png *.webp *.mp4 *.mov *.m4v *.JPG *.PNG *.MOV *.MP4"), ("All files", "*.*")]),
    "any": ("Choose a file", [("All files", "*.*")]),
}


def pick(kind: str) -> str:
    title, types = KINDS.get(kind, KINDS["any"])
    if sys.platform == "darwin":
        script = f'POSIX path of (choose file with prompt "{title}")'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return r.stdout.strip()
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # otherwise the window can hide behind the browser
    root.update()
    path = filedialog.askopenfilename(parent=root, title=title, filetypes=types)
    root.destroy()
    return path or ""


if __name__ == "__main__":
    print(pick(sys.argv[1] if len(sys.argv) > 1 else "any"))
