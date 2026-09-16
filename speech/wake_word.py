class WakeWord:
    def __init__(self, word="джарвис", enabled=True): self.word,self.enabled=word.lower(),enabled
    def strip(self,text):
        if not self.enabled: return text.strip()
        lower=text.lower(); pos=lower.find(self.word)
        return text[pos+len(self.word):].lstrip(" ,.!?") if pos >= 0 else None
