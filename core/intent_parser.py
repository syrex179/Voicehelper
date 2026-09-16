import re
from .nlp_utils import number
from .models import Command

class IntentParser:
    def __init__(self, memory): self.memory=memory
    def parse(self, text):
        raw=text.strip(); t=raw.lower()
        t=re.sub(r"^(?:джарвис|jarvis)[,\s]+", "", t)
        if re.fullmatch(r"(?:что ты умеешь|что умеешь|помощь|помоги|какие команды(?: ты знаешь)?)\??",t): return Command("HELP",{},raw)
        if re.fullmatch(r"(?:что ты сделал|что ты только что сделал|что было последним действием)\??",t): return Command("SESSION_RECAP",{},raw)
        if re.fullmatch(r"(?:что ты сказал|повтори ответ|повтори что ты сказал|повтори последнее сообщение)\??",t): return Command("SESSION_RESPONSE_REPEAT",{},raw)
        if re.fullmatch(r"(?:что ты сейчас жд[её]шь|что тебе нужно|что ты жд[её]шь)\??",t): return Command("PENDING_DIALOGUE_RECAP",{},raw)
        if re.fullmatch(r"(?:ты слушаешь|ты сейчас слушаешь|какой у тебя статус|ты на паузе)\??",t): return Command("SESSION_STATUS_QUERY",{},raw)
        if re.fullmatch(r"(?:что я могу сейчас сказать|что можно сейчас сделать|что ты сейчас от меня жд[её]шь|как я могу с тобой сейчас говорить)\??",t): return Command("SESSION_CONTEXTUAL_HELP",{},raw)
        if re.fullmatch(r"(?:понял|понятно|ясно|хорошо|ладно|принято)",t): return Command("SESSION_ACKNOWLEDGEMENT",{},raw)
        if re.fullmatch(r"(?:привет|здравствуй|добрый день)",t): return Command("SESSION_GREETING",{},raw)
        if re.fullmatch(r"(?:спасибо|благодарю|спс)",t): return Command("SESSION_THANKS",{},raw)
        if re.fullmatch(r"(?:пока|до свидания|до встречи)",t): return Command("SESSION_GOODBYE",{},raw)
        if re.fullmatch(r"(?:отмена|отменить|отмени)",t): return Command("CANCEL_PENDING_DIALOGUE",{},raw)
        if re.fullmatch(r"подожди",t): return Command("SESSION_PAUSE",{},raw)
        if re.fullmatch(r"(?:продолжай|слушай дальше)",t): return Command("SESSION_RESUME",{},raw)
        if re.fullmatch(r"(?:начн[её]м сначала|сбрось разговор)",t): return Command("SESSION_RESET",{},raw)
        if re.search(r"(?:начни|начать) обучение",t): return Command("START_DEMONSTRATION",{},raw)
        if re.search(r"(?:научись|научи(?: себя| тебя)?\s*,?\s*как|хочу тебя научить|запомни,?\s+как\s+я|запомни\s+(?:эту\s+)?последовательность|давай\s+я\s+покажу)",t): return Command("START_ROUTINE_LEARNING",{},raw)
        if re.fullmatch(r"(?:стоп|хватит|останови выполнение|отмени процедуру|прекрати)",t): return Command("CANCEL_ROUTINE_EXECUTION",{},raw)
        if re.search(r"(?:покажи мои (?:запланированные процедуры|расписания)|список расписаний|что у меня запланировано|какие процедуры запланированы)",t): return Command("LIST_ROUTINE_SCHEDULES",{},raw)
        if re.search(r"когда она запустится в следующий раз",t): return Command("NEXT_ROUTINE_SCHEDULE",{"pronoun":True},raw)
        match=re.search(r"когда следующая\s+(.+?)(?:\?|$)",t)
        if match: return Command("NEXT_ROUTINE_SCHEDULE",{"name":match.group(1).strip(" .?")},raw)
        match=re.search(r"(?:перенеси|измени расписание)\s+(завтрашнюю\s+)?(.+?)\s+на\s+(?:каждый день\s+в\s+)?(\d{1,2}:\d{2})$",t)
        if match:
            one_time=bool(match.group(1)); definition={"type":"daily","time":match.group(3)} if "каждый день" in t else None
            return Command("UPDATE_ROUTINE_SCHEDULE",{"name":match.group(2).strip(),"one_time":one_time,"update":{"schedule":definition} if definition else {"time":match.group(3)}},raw)
        match=re.search(r"измени расписание\s+(.+?)\s+на\s+каждый\s+(понедельник|вторник|среду|среда|четверг|пятницу|пятница|субботу|суббота|воскресенье)\s+в\s+(\d{1,2}:\d{2})$",t)
        if match:
            weekdays={"понедельник":0,"вторник":1,"среду":2,"среда":2,"четверг":3,"пятницу":4,"пятница":4,"субботу":5,"суббота":5,"воскресенье":6}
            return Command("UPDATE_ROUTINE_SCHEDULE",{"name":match.group(1).strip(),"update":{"schedule":{"type":"weekly","weekday":weekdays[match.group(2)],"time":match.group(3)}}},raw)
        match=re.search(r"(?:удали|отключи|включи)(?:\s+расписание)?\s+(.+?)(?:\s+для\s+(.+))?(?:\s+по расписанию)?$",t)
        if match and ("расписан" in t or t.startswith("отключи") or t.startswith("включи")):
            intent="DELETE_ROUTINE_SCHEDULE" if t.startswith("удали") else "DISABLE_ROUTINE_SCHEDULE" if t.startswith("отключи") else "ENABLE_ROUTINE_SCHEDULE"
            return Command(intent,{"name":match.group(1).strip(" ."),"preset":(match.group(2) or "").strip(" .")},raw)
        match=re.search(r"(?:удали расписание|отключи)\s+(.+?)(?:\s+по расписанию)?$",t)
        if match: return Command("DELETE_ROUTINE_SCHEDULE" if "удали" in t else "DISABLE_ROUTINE_SCHEDULE",{"name":match.group(1).strip(" .")},raw)
        match=re.search(r"включи расписание\s+(.+)$",t)
        if match: return Command("ENABLE_ROUTINE_SCHEDULE",{"name":match.group(1).strip(" .")},raw)
        weekdays={"понедельник":0,"вторник":1,"среду":2,"среда":2,"четверг":3,"пятницу":4,"пятница":4,"субботу":5,"суббота":5,"воскресенье":6}
        match=re.search(r"кажд(?:ый день|ое утро)\s+(?:в\s+)?(\d{1,2}:\d{2})\s+(?:запускай|запусти)\s+(.+?)(?:\s+с настройками\s+(.+))?$",t)
        if match: return Command("CREATE_ROUTINE_SCHEDULE",{"name":match.group(2).strip(),"schedule":{"type":"daily","time":match.group(1)},"preset":(match.group(3) or "").strip()},raw)
        match=re.search(r"(?:запускай|запусти)\s+(.+?)\s+каждый день\s+(?:в\s+)?(\d{1,2}:\d{2})(?:\s+с настройками\s+(.+))?$",t)
        if match: return Command("CREATE_ROUTINE_SCHEDULE",{"name":match.group(1).strip(),"schedule":{"type":"daily","time":match.group(2)},"preset":(match.group(3) or "").strip()},raw)
        match=re.search(r"каждый день\s+(?:запускай|запусти)\s+(.+?)\s+(?:в\s+)?(\d{1,2}:\d{2})(?:\s+с настройками\s+(.+))?$",t)
        if match: return Command("CREATE_ROUTINE_SCHEDULE",{"name":match.group(1).strip(),"schedule":{"type":"daily","time":match.group(2)},"preset":(match.group(3) or "").strip()},raw)
        match=re.search(r"каждый\s+(понедельник|вторник|среду|среда|четверг|пятницу|пятница|субботу|суббота|воскресенье)\s+(?:в\s+)?(\d{1,2}:\d{2})\s+(?:запускай|запусти)\s+(.+)$",t)
        if match: return Command("CREATE_ROUTINE_SCHEDULE",{"name":match.group(3).strip(),"schedule":{"type":"weekly","weekday":weekdays[match.group(1)],"time":match.group(2)}},raw)
        match=re.search(r"(?:запускай|запусти)\s+(.+?)\s+каждый\s+(понедельник|вторник|среду|среда|четверг|пятницу|пятница|субботу|суббота|воскресенье)\s+(?:в\s+)?(\d{1,2}:\d{2})$",t)
        if match: return Command("CREATE_ROUTINE_SCHEDULE",{"name":match.group(1).strip(),"schedule":{"type":"weekly","weekday":weekdays[match.group(2)],"time":match.group(3)}},raw)
        match=re.search(r"(?:поставь|запусти)\s+(.+?)\s+(?:на завтра|завтра)\s+(?:в\s+)?(\d{1,2}:\d{2})$",t)
        if match:
            from datetime import datetime, timedelta
            target=(datetime.now().astimezone()+timedelta(days=1)).replace(hour=int(match.group(2).split(':')[0]),minute=int(match.group(2).split(':')[1]),second=0,microsecond=0)
            return Command("CREATE_ROUTINE_SCHEDULE",{"name":match.group(1).strip(),"schedule":{"type":"once","at":target.isoformat()}},raw)
        informational=re.search(r"(?:расскажи про|что входит в|покажи действия(?: процедуры|)?|покажи процедуру)\s+(.+)",t)
        if informational:
            name=re.split(r"\s+(?:без|не открывай|пропусти)\s+",informational.group(1).strip(" .?"))[0]
            return Command("SHOW_ROUTINE",{"name":name},raw)
        match=re.search(r"(?:какую процедуру я обычно использую|что я обычно запускаю|какая у меня самая используемая процедура)(?:\s+(.+))?",t)
        if match: return Command("ROUTINE_RECOMMENDATION_INFO",{"context":(match.group(1) or "").strip()},raw)
        if "как обычно" in t or "обычную" in t:
            body=t; options={}; volume=re.search(r"(?:\s+и)?\s+поставь\s+громкость\s+(\d{1,3})",body)
            if volume: options["level"]=int(volume.group(1)); body=body[:volume.start()]+body[volume.end():]
            skip_match=re.search(r"\s+(?:без|не открывай|пропусти)\s+(.+?)(?=\s+(?:и|но)\s+|$)",body); skip=skip_match.group(1).strip() if skip_match else ""
            if skip_match: body=body[:skip_match.start()]+body[skip_match.end():]
            preset_match=re.search(r"\s+с настройками\s+(.+)$",body); preset=preset_match.group(1).strip() if preset_match else ""
            if preset_match: body=body[:preset_match.start()]
            value_match=re.search(r"\s+для\s+(.+)$",body); value=value_match.group(1).strip() if value_match else ""
            if value_match: body=body[:value_match.start()]
            context=re.sub(r"\b(?:сделай|запусти|мою|обычную|подготовь|меня|как|обычно)\b","",body).strip()
            specific=next((routine for routine in self.memory.learned_routines if re.search(r"\b"+re.escape(routine["name"].lower())+r"\b",t) or any(re.search(r"\b"+re.escape(alias.lower())+r"\b",t) for alias in routine.get("aliases",[]))),None)
            if specific: return Command("RUN_ROUTINE_COMPOSITE",{"query":specific["name"],"skip":skip,"value":value,"options":options},raw)
            return Command("RUN_ROUTINE_AS_USUAL",{"context":context,"skip":skip,"value":value,"options":options,"preset":preset},raw)
        match=re.search(r"когда я последний раз запускал\s+(.+)",t)
        if match: return Command("ROUTINE_HISTORY_LAST",{"name":match.group(1).strip(" .")},raw)
        match=re.search(r"как прош[её]л последний запуск\s+(.+)",t)
        if match: return Command("ROUTINE_HISTORY_LAST_RESULT",{"name":match.group(1).strip(" .")},raw)
        match=re.search(r"почему последний запуск\s+(.+?)(?:\s+завершился ошибкой)?$",t)
        if match: return Command("ROUTINE_HISTORY_FAILURE",{"name":match.group(1).strip(" .")},raw)
        match=re.search(r"покажи историю\s+(.+)",t)
        if match: return Command("ROUTINE_HISTORY_LIST",{"name":match.group(1).strip(" .")},raw)
        match=re.fullmatch(r"(.+?)\s+для\s+(.+?)(?:\s+(?:без|не открывай|пропусти)\s+(.+))?",t)
        if match and not re.match(r"(?:добавь (?:алиас|фразу)|удали алиас|переименуй процедуру|удали процедуру)\b",t): return Command("RUN_ROUTINE_COMPOSITE",{"query":match.group(1).strip(),"value":match.group(2).strip(),"skip":(match.group(3) or "").strip(),"options":{}},raw)
        match=re.fullmatch(r"(?:запусти\s+)?(.+?)\s+с настройками\s+(.+?)(?:\s+(?:без|не открывай|пропусти)\s+(.+))?",t)
        if match: return Command("RUN_ROUTINE_WITH_PRESET",{"query":match.group(1).strip(),"preset":match.group(2).strip(),"skip":(match.group(3) or "").strip(),"options":{}},raw)
        match=re.search(r"(.+?)\s+(?:без|не открывай|пропусти)\s+(.+)",t)
        if match: return Command("RUN_ROUTINE_WITH_SKIP",{"query":match.group(1).strip(),"skip":match.group(2).strip(" .")},raw)
        if re.search(r"(?:покажи мои процедуры|какие у меня есть процедуры|список обученных процедур)",t): return Command("LIST_ROUTINES",{},raw)
        match=re.search(r"добавь (?:алиас|фразу)\s+[«\"]?(.*?)[»\"]?\s+(?:для|к)\s+процедур(?:е|ы)?\s+(.+)",t)
        if match: return Command("ADD_ROUTINE_ALIAS",{"alias":match.group(1).strip(),"name":match.group(2).strip(" .")},raw)
        match=re.search(r"удали алиас\s+[«\"]?(.*?)[»\"]?\s+у\s+процедур(?:ы|е)\s+(.+)",t)
        if match: return Command("REMOVE_ROUTINE_ALIAS",{"alias":match.group(1).strip(),"name":match.group(2).strip(" .")},raw)
        match=re.search(r"переименуй процедуру (.+?) в (.+)",t)
        if match: return Command("RENAME_ROUTINE",{"old":match.group(1).strip(),"new":match.group(2).strip()},raw)
        match=re.search(r"удали процедуру (.+)",t)
        if match: return Command("DELETE_ROUTINE",{"name":match.group(1).strip()},raw)
        if re.search(r"(?:закончи|закончить) обучение",t): return Command("STOP_DEMONSTRATION",{},raw)
        if re.search(r"отмени обучение",t): return Command("CANCEL_DEMONSTRATION",{},raw)
        for alias, target in self.memory.aliases.items(): t=re.sub(r"\b"+re.escape(alias)+r"(?:а|у|ом|е|и)?\b", target.lower(), t)
        for phrase, skill_name in self.memory.custom_commands.items():
            if t==phrase.lower(): return Command("RUN_CUSTOM_COMMAND",{"skill":skill_name},raw)
        for routine in getattr(self.memory,"learned_routines",[]):
            haystack=[routine["name"].lower()]+[x.lower() for x in routine.get("aliases",[])]
            if t in haystack: return Command("RUN_ROUTINE",{"routine":routine["name"]},raw)
        alias=re.search(r"(?:я называю|называй|запомни,? что)\s+(.+?)\s+(?:это|—|-)\s+(.+?)[.!]?$",t)
        if not alias:
        
            alias=re.search(r"(?:я называю|называй)\s+(.+?)\s+([^\s.]+)[.!]?$",t)
        if alias: return Command("SET_ALIAS",{"target":alias.group(1).strip(),"alias":alias.group(2).strip()},raw)
        create=re.search(r"создай (?:новый )?(?:режим|скилл)\s+([^:.]+)(?::\s*(.+))?",t)
        if create: return Command("CREATE_SKILL",{"name":create.group(1).strip().title(),"description":create.group(2) or ""},raw,entities={"skill_name":create.group(1).strip().title()})
        if re.search(r"создай (?:новый )?(?:режим|скилл)",t): return Command("CREATE_SKILL",{},raw)
        if re.search(r"научи тебя новому режиму",t): return Command("CREATE_SKILL",{},raw)
        custom=re.search(r"создай команду\s+[«\"]?(.+?)[»\"]?\s+для режима\s+(.+?)[.!]?$",t)
        if custom: return Command("CREATE_CUSTOM_COMMAND",{"phrase":custom.group(1).strip(),"skill":custom.group(2).strip()},raw)
        custom=re.search(r"создай команду\s+[«\"]?(.+?)[»\"]?[.!]?$",t)
        if custom: return Command("CREATE_CUSTOM_COMMAND",{"phrase":custom.group(1).strip(),"skill":None},raw)
        match=re.search(r"(?:измени режим|редактируй режим)\s+(.+)",t)
        if match: return Command("EDIT_SKILL",{"name":match.group(1).strip(" .")},raw)
        match=re.search(r"(?:забудь|удали) режим\s+(.+)",t)
        if match: return Command("DELETE_SKILL",{"name":match.group(1).strip(" .")},raw)
        if re.fullmatch(r"(?:установи|поставь|сделай)\s+громкость",t): return Command("INCOMPLETE_SET_VOLUME",{},raw)
        if re.search(r"громкост|(?:сделай|установи|уменьши|увеличь).*звук",t):
            level=number(t)
            if level is None or not 0<=level<=100: return Command("INVALID_VOLUME",{},raw)
            return Command("SET_VOLUME",{"level":level},raw,entities={"volume":level})
        rules=[
          ("TAKE_SCREENSHOT",r"скриншот|сделай скрин|сфотографируй экран"),("VOLUME_UP",r"громче"),("VOLUME_DOWN",r"тише"),("MUTE",r"выключи звук|без звука"),("UNMUTE",r"включи звук"),
          ("PLAY_PAUSE",r"пауза|продолжи"),("NEXT_TRACK",r"следующ.*трек"),("PREVIOUS_TRACK",r"предыдущ.*трек"),("SHOW_DESKTOP",r"рабочий стол|сверни все"),
          ("OPEN_SETTINGS",r"открой настройки"),("OPEN_TASK_MANAGER",r"диспетчер задач"),("LOCK",r"заблокируй"),("SHUTDOWN",r"выключи компьютер"),("RESTART",r"перезагрузи"),
          ("OPEN_YOUTUBE",r"(?:открой|зайди|перейди).*?(?:youtube|ютуб)"),("SEARCH_YOUTUBE",r"(?:найди|включи).*(?:youtube|ютуб)"),
          ("SEARCH_WEB",r"(?:найди|ищи) в (?:google|гугл|интернете)"),("OPEN_BROWSER",r"(?:открой|запусти) (?:браузер|интернет)"),
          ("OPEN_FOLDER",r"открой (?:загрузки|документы|рабочий стол)"),("SEARCH_FILE",r"(?:найди|поищи).*(?:файл|pdf|отч[её]т)"),
        ]
        for intent, pattern in rules:
            if re.search(pattern,t):
                query=re.sub(r".*?(?:найди|включи) (?:на )?(?:youtube|ютуб)\s*", "", t).strip()
                return Command(intent,{"query":query},raw)
        if re.fullmatch(r"(?:открой|запусти|перейди на)\s+(?:сайт|веб-?сайт|страницу)",t): return Command("INCOMPLETE_OPEN_SITE",{},raw)
        if re.fullmatch(r"открой\s+(?:папку|каталог)",t): return Command("INCOMPLETE_OPEN_FOLDER",{},raw)
        url_match=re.fullmatch(r"(?:открой|запусти)\s+(https?://[^\s]+)",t)
        if url_match: return Command("OPEN_BROWSER",{"url":url_match.group(1)},raw)
        skill=re.match(r"(?:запусти|включи|начни) (?:режим )?(.+)",t)
        if skill and self.memory.skills and any(x["name"].lower()==skill.group(1).strip() or x["trigger"].lower()==skill.group(1).strip() for x in self.memory.skills): return Command("RUN_SKILL",{"skill":skill.group(1).strip()},raw,entities={"skill_name":skill.group(1).strip()})
        m=re.search(r"(?:открой|запусти|включи|можешь открыть)(?: мне)?\s+(.+)",t)
        if m:
            value=m.group(1).strip(" ?.!")
            suggestions={"дис":"discord","дискор":"discord","тел":"telegram","стимм":"steam"}
            if value in suggestions: return Command("AMBIGUOUS_APPLICATION",{"application":suggestions[value]},raw,entities={"query":value,"confidence":"LOW"})
            pieces=re.split(r"\s*(?:,|\bи\b)\s*",value)
            if len(pieces)>1: return Command("MULTI_ACTION",{},raw,actions=[{"intent":"OPEN_APPLICATION","parameters":{"application":p}} for p in pieces if p])
            return Command("OPEN_APPLICATION",{"application":value},raw,entities={"app_name":value,"confidence":"HIGH"})
        return Command("UNKNOWN",{},raw)
