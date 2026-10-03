"""Original bilingual fixtures; known perturbations are independent ground truth."""
import json
from dataclasses import replace
from pathlib import Path
from app.services.alignment import Cue, write_preview, sha256

PAIRS = [
('The red suitcase belongs to my sister.','Czerwona walizka należy do mojej siostry.'),
('We must leave before the bridge closes.','Musimy wyjść zanim zamkną most.'),
('Someone hid the letter beneath the clock.','Ktoś ukrył list pod zegarem.'),
('My father worked in this hospital.','Mój ojciec pracował w tym szpitalu.'),
('The train arrives tomorrow at noon.','Pociąg przyjeżdża jutro w południe.'),
('Please put the blue glass on the shelf.','Proszę postaw niebieską szklankę na półce.'),
('I have never seen snow in July.','Nigdy nie widziałem śniegu w lipcu.'),
('There are seven keys inside that box.','W tamtym pudełku jest siedem kluczy.'),
('Our neighbor forgot to feed the cat.','Nasz sąsiad zapomniał nakarmić kota.'),
('You promised to call me from Paris.','Obiecałeś zadzwonić do mnie z Paryża.'),
('The garden gate was open all night.','Brama ogrodu była otwarta przez całą noc.'),
('I found your photograph in the attic.','Znalazłem twoje zdjęcie na strychu.'),
('Do not touch the broken window.','Nie dotykaj rozbitego okna.'),
('The judge asked for another witness.','Sędzia poprosił o kolejnego świadka.'),
('Her brother sold the old fishing boat.','Jej brat sprzedał starą łódź rybacką.'),
('The children are sleeping upstairs.','Dzieci śpią na górze.'),
('This map shows a tunnel under the river.','Ta mapa pokazuje tunel pod rzeką.'),
('We brought enough food for three days.','Przynieśliśmy jedzenie na trzy dni.'),
('The museum will reopen next Tuesday.','Muzeum otworzą ponownie w przyszły wtorek.'),
('Your coat is hanging behind the door.','Twój płaszcz wisi za drzwiami.'),
('They heard a strange noise in the cellar.','Usłyszeli dziwny hałas w piwnicy.'),
('The doctor recommended a longer holiday.','Lekarz zalecił dłuższy urlop.'),
('I ordered coffee without any sugar.','Zamówiłem kawę bez cukru.'),
('Nobody remembered the password.','Nikt nie pamiętał hasła.'),
('We saw two foxes beside the road.','Widzieliśmy dwa lisy przy drodze.'),
('She teaches history at the university.','Ona uczy historii na uniwersytecie.'),
('The captain refused to abandon his ship.','Kapitan odmówił opuszczenia statku.'),
('He repaired the bicycle with a small wrench.','Naprawił rower małym kluczem.'),
('The last bus has already departed.','Ostatni autobus już odjechał.'),
('Tomorrow we will plant an apple tree.','Jutro posadzimy jabłoń.')]


def generate(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    en=[Cue(f'english:{i+1}',i+1,5000+(i//2)*40000+(i%2)*2500,6800+(i//2)*40000+(i%2)*2500,1800,a,a,'english') for i,(a,b) in enumerate(PAIRS)]
    truth=[replace(c,cue_id=f'polish:{i+1}',raw_text=PAIRS[i][1],normalized_text=PAIRS[i][1],source='polish') for i,c in enumerate(en)]
    cases=[]
    for name in ['offset','drift','cut','en_groups','pl_groups','sdh','ocr','wrong_film']:
        english=en[:]; correct=truth[:]
        if name=='en_groups':
            english=[replace(en[i],sequence=j+1,end_ms=en[i+1].end_ms,raw_text=en[i].raw_text+' '+en[i+1].raw_text) for j,i in enumerate(range(0,30,2))]
        if name=='pl_groups':
            correct=[replace(truth[i],sequence=j+1,cue_id=f'polish:{j+1}',end_ms=truth[i+1].end_ms,raw_text=truth[i].raw_text+' '+truth[i+1].raw_text) for j,i in enumerate(range(0,30,2))]
        if name=='sdh':english=[replace(c,raw_text='[MUSIC]\nJOHN: '+c.raw_text) for c in english]
        if name=='ocr':english=[replace(c,raw_text=c.raw_text.replace('the','tbe').replace('I ','l ')) for c in english]
        if name=='wrong_film':correct=[replace(c,raw_text=f'Receptura numer {i}: dodaj mąkę i wymieszaj składniki.') for i,c in enumerate(correct)]
        def wrong(t,i):
            if name=='drift': return round(t/1.025+4000)
            if name=='cut':return t+8000+(12000 if i>=15 else 0)
            return t+8000
        polish=[replace(c,start_ms=wrong(c.start_ms,i),end_ms=wrong(c.end_ms,i)) for i,c in enumerate(correct)]
        folder=root/name;folder.mkdir(exist_ok=True)
        write_preview(english,folder/'english.srt');write_preview(polish,folder/'polish.srt')
        annotations=[]
        for i,c in enumerate(correct):
            eis=[i] if name not in {'en_groups','pl_groups'} else [i//2] if name=='en_groups' else [2*i,2*i+1]
            annotations.append({'polishIds':[f'polish:{i+1}'], 'englishIds':[f'english:{x+1}' for x in eis] if name!='wrong_film' else [],
                'startMs':c.start_ms,'endMs':c.end_ms,'uncertain':False})
        if name=='en_groups':
            # Each annotation relation is grouped once, not duplicated for each PL cue.
            for i in range(0,30,2):
                annotations[i]['polishIds']=[f'polish:{i+1}',f'polish:{i+2}']
                annotations[i]['endMs']=correct[i+1].end_ms
                annotations[i+1]['relationOnly']=False
                annotations[i+1]['englishIds']=[]
                annotations[i+1]['relationExcluded']=True
        if name=='wrong_film':
            for point in annotations:
                point.pop('startMs',None);point.pop('endMs',None)
        (folder/'annotations.json').write_text(json.dumps({'scope':'negative_no_correspondence' if name=='wrong_film' else 'full_synthetic','points':annotations},ensure_ascii=False,indent=2))
        cases.append({'id':name,'category':name,'split':'tuning' if name in {'offset','drift'} else 'evaluation',
            'kind':'synthetic','identity':{'title':'Original bilingual test dialogue','edition':name},
            'english':f'{name}/english.srt','polish':f'{name}/polish.srt','annotations':f'{name}/annotations.json',
            'englishSha256':sha256(folder/'english.srt'),'polishSha256':sha256(folder/'polish.srt'),
            'durationMs':650000,'expectedReject':name=='wrong_film'})
    manifest={'schema':'subtitle-benchmark-v1','cases':cases}
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    return root/'manifest.json'


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('output',type=Path);args=parser.parse_args();print(generate(args.output))
