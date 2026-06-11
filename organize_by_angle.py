# -*- coding: utf-8 -*-
import json, os, shutil
from collections import Counter

m=json.load(open(r'C:/Users/Thang/Downloads/MetaAdLibrary/_ultimate/manifest.json',encoding='utf-8'))

HAIR_MAP = {
 "AI Hair Makeover & Best Look": ["Look more elegant with AI hair","AI finds your best look","Unlock your best style with AI"],
 "Most Flattering Hairstyles (1-Click)": ["Most flattering hairstyles in 1 click","Most flattering hairstyles","Find your perfect hairstyle"],
 "Face Shape Analysis": ["Check your face shape first","Know your face shape","Hairstyle for your exact face shape","Match hair to your face shape"],
 "Try Before the Salon": ["AI hairstyle try-on","Try hairstyles before the salon","Try before the salon","Try styles before the chop","Try before you cut"],
 "Try Many Styles, No Haircut": ["Try dozens of hairstyles","Try dozens of hairstyles on AI","Try 20 hairstyles, no haircut","Bangs, bob or pixie?","Bob, pixie or bangs?","Bangs or no bangs? AI decides"],
 "Beard Matching": ["Match your beard to your hair","Your beard just doesn't match"],
 "Stylist Guide & Hair Color": ["Stylist-recommended hair finder","Ultimate hairstyle guide","Find your best hair color"],
}
HOME_MAP = {
 "AI Interior Redesign (Free)": ["Design it with AI, free","AI interior redesign","Design your dream home","Any room, any style you love"],
 "Small Room to 10x Bigger": ["Small room feels 10x bigger"],
 "Make Home Look Expensive": ["Make your home look expensive","From plain to designer home"],
 "Floor Plan to 3D Layout": ["Floor plan to 3D layout","VR home layout redesign"],
 "Backyard Redesign": ["Backyard redesigned with AI","AI Remodel your backyard","Backyard redesigned in seconds"],
 "One-Tap Makeover & #1 Designer": ["One-tap AI room makeover","AI Remodel your room","#1 AI interior designer","Ditch Pinterest \u2014 AI design inspiration","Ditch Pinterest \u2014 AI design"],
}
ROOT={'hair':r'D:/Dev/Apps Detail/Chatbot Lite2/Video 0406','home':r'D:/Dev/Apps Detail/DecoAI/ADS/video 0406'}

def a2c(vert):
    mp=HAIR_MAP if vert=='hair' else HOME_MAP
    d={}
    for c,angs in mp.items():
        for a in angs: d[a.strip()]=c
    return d

stats={'hair':Counter(),'home':Counter()}
copied={'hair':0,'home':0}
unmapped=Counter()
for v in m['videos']:
    vert=v.get('vertical')
    if vert not in ('hair','home'): continue
    outs=v.get('outputs') or {}
    if not outs: continue
    camp=a2c(vert).get((v.get('angle') or '').strip())
    if camp is None:
        unmapped[(vert,v.get('angle'))]+=1; continue
    root=os.path.join(ROOT[vert],'_by_angle',camp)
    for lang,path in outs.items():
        if not path or not os.path.exists(path): continue
        langfolder=os.path.basename(os.path.dirname(path))
        dstdir=os.path.join(root,langfolder)
        os.makedirs(dstdir,exist_ok=True)
        dst=os.path.join(dstdir,os.path.basename(path))
        if not (os.path.exists(dst) and os.path.getsize(dst)>100000):
            shutil.copy2(path,dst)
        copied[vert]+=1
        stats[vert][camp]+=1

for vert in ('hair','home'):
    print(f"=== {vert}  copied={copied[vert]}")
    for c,n in stats[vert].most_common(): print(f"  {n:4d}  {c}")
if unmapped:
    print("UNMAPPED:")
    for k,n in unmapped.items(): print("  ",n,k)
