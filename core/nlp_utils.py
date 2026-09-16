import re

ORDINALS={"первое":1,"первый":1,"первого":1,"второе":2,"второй":2,"второго":2,"третье":3,"третий":3,"третьего":3,"четвёртое":4,"четвертое":4,"четвёртый":4,"четвертый":4,"четвёртого":4,"четвертого":4,"пятое":5,"пятый":5,"пятого":5}
NUMBERS={"ноль":0,"один":1,"два":2,"три":3,"четыре":4,"пять":5,"шесть":6,"семь":7,"восемь":8,"девять":9,"десять":10,"двадцать":20,"тридцать":30,"сорок":40,"пятьдесят":50,"шестьдесят":60,"семьдесят":70,"восемьдесят":80,"девяносто":90,"сто":100}
def number(text):
    match=re.search(r"-?\d{1,3}",text)
    if match: return int(match.group())
    words=re.findall(r"[а-яё]+",text.lower()); total=0; found=False
    for word in words:
        if word in NUMBERS: total+=NUMBERS[word]; found=True
    return total if found else None
def ordinal(text):
    value=number(text)
    if value is not None: return value
    for word,value in ORDINALS.items():
        if word in text.lower(): return value
    return None
