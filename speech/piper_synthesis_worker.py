"""Isolated Python 3.12 Piper synthesis helper used only by TextToSpeech."""
import argparse
from pathlib import Path
import sys
import wave


def main():
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument("--model",required=True)
    parser.add_argument("--espeak-data",required=True)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    text=sys.stdin.read().strip()
    if not text: return 2
    from piper.voice import PiperVoice
    model=Path(args.model); data=Path(args.espeak_data); output=Path(args.output)
    if not model.is_file() or not data.is_dir(): return 2
    voice=PiperVoice.load(model,espeak_data_dir=data)
    with wave.open(str(output),"wb") as wav_file:
        voice.synthesize_wav(text,wav_file)
    return 0 if output.is_file() and output.stat().st_size>44 else 1


if __name__=="__main__": raise SystemExit(main())
