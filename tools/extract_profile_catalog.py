"""Extract profile indexes from installed UE3 assets; never infer indexes from list order.
Usage: PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tools/extract_profile_catalog.py GAME_DATA
Requires this editor's configured borderlands2-tool codec. Read-only game access.
"""
import struct,pathlib,json
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import save_io
from borderlands.datautil.lzo1x import lzo1x_decompress

def block_decode(b):
 sig,block,comp,total=struct.unpack_from('<4I',b);assert sig==0x9e2a83c1
 n=(total+block-1)//block;pos=16+n*8;out=bytearray()
 for z in range(n):
  c,u=struct.unpack_from('<2I',b,16+8*z);d=lzo1x_decompress(b'\xf0'+struct.pack('>I',u)+b[pos:pos+c]);pos+=c;assert len(d)==u;out+=d
 assert len(out)==total
 return bytes(out)
def parse(p):
 b=p.read_bytes()
 if struct.unpack_from('<I',b,4)[0]==0x20000:b=block_decode(b)
 # Mac BL2 version 832/licensee46 summary, None package group.
 assert b[12:21]==b'\x05\0\0\0None\0'
 n=struct.unpack_from('<I',b,85)[0];at=89+n*12+8;flags,count=struct.unpack_from('<II',b,at)
 if count:
  assert flags==2
  chunks=[struct.unpack_from('<4I',b,at+8+16*z) for z in range(count)]
  out=bytearray(b[:chunks[0][0]])
  for off,size,coff,csize in chunks:
   d=block_decode(b[coff:coff+csize]);assert len(d)==size
   if len(out)<off:out+=b'\0'*(off-len(out))
   out[off:off+size]=d
  b=bytes(out)
 u=lambda p:struct.unpack_from('<I',b,p)[0];i=lambda p:struct.unpack_from('<i',b,p)[0]
 names=[];pos=u(29)
 for z in range(u(25)):
  n=i(pos);pos+=4;size=abs(n)*(1 if n>0 else 2);names.append(b[pos:pos+size].decode('latin1' if n>0 else 'utf-16-le').rstrip('\0'));pos+=size+8
 imports=[];pos=u(45)
 for z in range(u(41)):
  imports.append(dict(outer=i(pos+16),name=names[u(pos+20)]));pos+=28
 exports=[];pos=u(37)
 for z in range(u(33)):
  exports.append(dict(cls=i(pos),outer=i(pos+8),name=names[u(pos+12)],size=i(pos+32),off=i(pos+36)))
  pos+=68+4*u(pos+44)
 assert pos==u(49)
 def path(idx,depth=0):
  if idx==0:return ''
  assert depth<50
  e=exports[idx-1] if idx>0 else imports[-idx-1];pre=path(e['outer'],depth+1)
  return pre+'.'+e['name'] if pre else e['name']
 result=[]
 for z,e in enumerate(exports):
  if path(e['cls']).endswith('.CustomizationDefinition'):
   raw=b[e['off']:e['off']+e['size']];vals={}
   for prop in ('ProfileEntryID','ProfileEntryIndex'):
    if prop not in names:continue
    tag=struct.pack('<II',names.index(prop),0)+struct.pack('<II',names.index('IntProperty'),0)+struct.pack('<II',4,0)
    at=raw.find(tag)
    if at>=0:vals[prop]=struct.unpack_from('<i',raw,at+24)[0]
   result.append(dict(path=path(z+1),**vals))
 return result

if __name__ == '__main__':
 import sys,re,hashlib
 root=pathlib.Path(sys.argv[1]);repo=pathlib.Path(__file__).resolve().parents[1]
 catalog=json.loads((repo/'gibbed_data/Customizations.json').read_text(encoding='utf-8-sig'))
 all_defs={};provenance=[]
 files=[root/'WillowGame/CookedPCConsole/Startup.upk']+sorted((root/'DLC').rglob('*Startup_SF.upk'))
 for p in files:
  for d in parse(p):
   if d['path'] in all_defs:assert all_defs[d['path']]==d
   all_defs[d['path']]=d
  provenance.append({'package':str(p.relative_to(root)),'sha1':hashlib.sha1(p.read_bytes()).hexdigest()})
 classes={'Soldier':'Axton','Assassin':'Zer0','Siren':'Maya','Mercenary':'Salvador','Mechromancer':'Gaige','Psycho':'Krieg'}
 records=[]
 supplemental=json.loads((repo/'head_sources.json').read_text())
 source_lookup={(cls,name.casefold()):group for group in supplemental['groups'] for cls,name in group['heads'].items()}
 for asset,meta in sorted(catalog.items()):
  usages=[u for u in meta['usage'] if u in classes]
  if len(usages)!=1:continue
  props=all_defs.get(asset,{});index=props.get('ProfileEntryIndex');pack=asset.split('.')[0].removeprefix('GD_AllCustoms_').lower()
  default=asset.startswith('GD_DefaultCustoms_');base=pack in ('maingame','tulip','lilac');group=None
  dlc=meta.get('dlc','')
  if not base and not default:
   group=dlc.rsplit('.',1)[-1].lower()
   group=re.sub(r'_(assassin|soldier|mechro|mercenary|siren)(power|cool|zany)$',r'_\2',group)
   group=re.sub(r'_(zero|soldier|mechro|merc|siren|psycho)hp', '_hp',group)
  if asset.endswith('HJ'):group='theme_handsome_jack_mask'
  if asset.endswith('MC'):group='theme_minecraft'
  source=source_lookup.get((classes[usages[0]],meta['name'].casefold()))
  tier=0 if default else (2 if base else 4)
  if meta['name'].startswith('Vault Veteran:'):
   group='promotion_borderlands1';tier=4
  elif meta['name'].startswith('Special Edition:'):
   group='promotion_special_edition';tier=4
  if source is not None:
   group=source['group'];tier=source['tier']
  records.append({'asset':asset,'name':meta['name'],'kind':meta['type'].lower(),'class':classes[usages[0]],
                  'profile_id':props.get('ProfileEntryID',300),'index':index,'pack':pack,'group':group,
                  'tier':tier,'tier_basis':'verified_source' if source else 'pack_proxy',
                  'availability':'bitmap' if index is not None else ('default' if default or base else 'entitlement'),
                  'verified':asset in all_defs or default})
 for cls,name in source_lookup:
  assert any(d['class']==cls and d['name'].casefold()==name for d in records),(cls,name)
 out={'schema':1,'sources':provenance,'customizations':records}
 (repo/'profile_customizations.json').write_text(json.dumps(out,indent=2)+'\n')
 print('catalog',len(records),'indexed',sum(x['index'] is not None for x in records))
 print('unverified',[x['asset'] for x in records if not x['verified']])
