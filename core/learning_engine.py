import re
from .models import Result

class LearningEngine:
    def __init__(self, parser, skills): self.parser,self.skills=parser,skills
    def extract_actions(self, explanation):
        text=explanation.lower(); actions=[]
        if "скрин" in text or "сфотографируй экран" in text: actions.append({"intent":"TAKE_SCREENSHOT","parameters":{}})
        if "ютуб" in text or "youtube" in text: actions.append({"intent":"OPEN_YOUTUBE","parameters":{}})
        if "браузер" in text and not any(a["intent"]=="OPEN_YOUTUBE" for a in actions): actions.append({"intent":"OPEN_BROWSER","parameters":{}})
        trigger=re.search(r'(?:когда я говорю|команд[ау])\s*[«"]?([^«".,]+)',text)
        # One natural-language clause may contain several applications: "открой OBS, Discord и Telegram".
        clauses=re.findall(r'(?:открой|запусти)\s+(.+?)(?=\s+потом\b|$)', text)
        for clause in clauses:
            for app in re.split(r'\s*(?:,|\bи\b)\s*', clause.strip(' ,.')):
                app=app.strip(' ,.')
                if app: actions.append({"intent":"OPEN_APPLICATION","parameters":{"application":app}})
        v=re.search(r'громкост[ьи].*?(\d{1,3})',text)
        if v: actions.append({"intent":"SET_VOLUME","parameters":{"level":min(100,int(v.group(1)))}})
        return actions
    def draft(self, explanation, name=None):
        text=explanation.lower(); actions=self.extract_actions(text)
        trigger=re.search(r'(?:когда я говорю|команд[ау])\s*[«"]?([^«".,]+)',text)
        name=(name or (trigger.group(1).strip() if trigger else "Новый режим")).title()
        return {"name":name,"trigger":name.lower(),"actions":actions}
    def create_from_explanation(self, explanation):
        draft=self.draft(explanation)
        if not draft["actions"]: return Result(False,"Я не распознал безопасных действий для режима.")
        skill=self.skills.create(**draft); return Result(True,f"Режим «{skill['name']}» создан.",{"skill":skill})
