#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Direct speech transcription script - using Whisper
"""

import sys
import os

def main():
    audio_file = "uploads/d5c85e8b.webm"
    
    print(f"Checking audio file: {audio_file}")
    if not os.path.exists(audio_file):
        print(f"Error: file not found {audio_file}")
        sys.exit(1)
    
    print(f"File size: {os.path.getsize(audio_file)} bytes")
    
    try:
        from pydub import AudioSegment
        import whisper
        
        # Convert to wav
        print("\n1. Converting audio format webm -> wav...")
        wav_file = "uploads/d5c85e8b_temp.wav"
        audio = AudioSegment.from_file(audio_file, format="webm")
        audio.export(wav_file, format="wav")
        print(f"   Successfully converted to: {wav_file}")
        
        # Load Whisper model
        print("\n2. Loading Whisper base model...")
        model = whisper.load_model("base")
        print("   Model loaded successfully")
        
        # Transcribe
        print("\n3. Starting transcription...")
        result = model.transcribe(wav_file)
        
        print("\n" + "="*50)
        print("Transcription result:")
        print("="*50)
        print(result["text"])
        print("="*50)
        
        # Save result
        output_file = "uploads/d5c85e8b_transcription.txt"
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(result["text"])
        print(f"\nResult saved to: {output_file}")
        
        # Clean up temp file
        if os.path.exists(wav_file):
            os.remove(wav_file)
            
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
