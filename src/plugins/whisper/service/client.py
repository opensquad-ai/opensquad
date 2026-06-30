"""
Whisper Service Client Example
Demonstrates how to call the API provided by whisper_service.py.
"""

import json

import requests


class WhisperClient:
    """Whisper service client"""

    def __init__(self, base_url="http://localhost:5001"):
        self.base_url = base_url

    def health_check(self):
        """Health check"""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            return response.json()
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def get_status(self):
        """Get service status"""
        response = requests.get(f"{self.base_url}/status", timeout=5)
        return response.json()

    def transcribe_file(self, audio_path, language=None, task="transcribe"):
        """
        Upload a file for transcription.

        Args:
            audio_path: Local audio file path.
            language: Language code (e.g. 'zh', 'en'); None for auto-detection.
            task: 'transcribe' or 'translate'.

        Returns:
            dict: {"success": True, "text": "transcription result", ...}
        """
        with open(audio_path, "rb") as f:
            files = {"file": f}
            data = {}
            if language:
                data["language"] = language
            if task:
                data["task"] = task

            response = requests.post(
                f"{self.base_url}/transcribe",
                files=files,
                data=data,
                timeout=300,  # 5-minute timeout
            )
            return response.json()

    def transcribe_by_path(self, audio_path, language=None, task="transcribe"):
        """
        Transcribe by file path (the service must be able to access this path).

        Suitable for scenarios where client and server are on the same machine.
        """
        payload = {"path": audio_path, "language": language, "task": task}
        response = requests.post(f"{self.base_url}/transcribe/url", json=payload, timeout=300)
        return response.json()


# ==================== Usage Examples ====================


def example_usage():
    """Usage examples"""
    client = WhisperClient("http://localhost:5001")

    # 1. Health check
    print("=== Health Check ===")
    health = client.health_check()
    print(json.dumps(health, indent=2, ensure_ascii=False))

    if health.get("status") != "healthy":
        print("Service not ready, please start whisper_service.py first")
        return

    # 2. View status
    print("\n=== Service Status ===")
    status = client.get_status()
    print(json.dumps(status, indent=2, ensure_ascii=False))

    # 3. Transcribe audio file (upload method)
    audio_file = "/path/to/test_audio.wav"  # Replace with actual test file
    print("\n=== Transcribe Audio (Upload) ===")
    print(f"File: {audio_file}")

    # # If the file exists, perform transcription
    import os
    # if os.path.exists(audio_file):
    #     result = client.transcribe_file(audio_file, language='zh')
    #     print(json.dumps(result, indent=2, ensure_ascii=False))
    #
    #     if result.get("success"):
    #         print(f"\nTranscription result: {result['text']}")
    #         print(f"Duration: {result['duration']} seconds")
    # else:
    #     print(f"File not found: {audio_file}")

    # 4. Transcribe by path (same-machine deployment, faster)
    print("\n=== Transcribe Audio (By Path) ===")
    if os.path.exists(audio_file):
        result = client.transcribe_by_path(audio_file, language="zh")
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    example_usage()
