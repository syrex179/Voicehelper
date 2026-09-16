"""One-shot SAPI environment control smoke; independent from JARVIS."""
import os


def _hresult(error):
    args = getattr(error, "args", ())
    value = getattr(error, "hresult", args[0] if args else None)
    return hex(value & 0xFFFFFFFF) if isinstance(value, int) else "NONE"


def main():
    os.environ["windir"] = os.environ.get("windir") or os.environ["SystemRoot"]
    os.environ["WINDIR"] = os.environ["windir"]
    os.environ["SystemRoot"] = os.environ.get("SystemRoot") or os.environ["windir"]
    voice_id = "NONE"
    output_count = 0
    output_id = "NONE"
    output_description = "NONE"
    current_output = "NONE"
    output_set = False
    result = "FAIL"
    error_code = "NONE"
    pythoncom = None
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        voice = win32com.client.Dispatch("SAPI.SpVoice")
        tokens = voice.GetVoices()
        if not tokens.Count:
            raise RuntimeError("SAPI_NO_VOICES")
        # This is a standalone verification only.  Use the first installed
        # token and exercise both requested strings through the same SAPI API.
        token = tokens.Item(0)
        voice.Voice = token
        voice_id = token.Id
        outputs = voice.GetAudioOutputs()
        output_count = outputs.Count
        try:
            current = voice.AudioOutput
            current_output = current.Id if current is not None else "NONE"
        except Exception:
            current_output = "NOT_AVAILABLE"
        if output_count:
            output = outputs.Item(0)
            output_id = output.Id
            output_description = output.GetDescription()
            voice.AudioOutput = output
            output_set = True
        voice.Speak("JARVIS voice test.")
        result = "SUCCESS"
    except Exception as error:
        error_code = _hresult(error)
    finally:
        if pythoncom is not None:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
    print("windir=" + os.environ["windir"])
    print("systemroot=" + os.environ["SystemRoot"])
    print("audio_output_count=" + str(output_count))
    print("audio_output_1_id=" + output_id)
    print("audio_output_1_description=" + output_description)
    print("current_audio_output=" + current_output)
    print("explicit_output_set=" + str(output_set).lower())
    print("sapi_speak=" + result)
    print("hresult=" + error_code)
    print("RESULT=" + result)
    return 0 if result == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
