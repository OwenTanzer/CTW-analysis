"""Reproduce a bounded, visually assessed Cold Mires comparison. No source mutation."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import numpy as np
from PIL import Image
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
from matplotlib.patches import Polygon, Patch
from matplotlib.lines import Line2D

ap=argparse.ArgumentParser(description=__doc__)
ap.add_argument('--data-root',type=Path,required=True)
ap.add_argument('--study-root',type=Path,default=Path(__file__).resolve().parents[2]/'studies/cold_mires_geographic_checkpoint')
args=ap.parse_args(); study=args.study_root; data=args.data_root
lock=json.loads((study/'source_lock.json').read_text()); ev=json.loads((study/'evidence.json').read_text())
sha=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
head=subprocess.check_output(['git','-C',str(data),'rev-parse','HEAD'],text=True).strip()
if head!=lock['commit']: raise ValueError('Wrong Data commit')
for p,h in lock['files'].items():
    if sha(data/p)!=h: raise ValueError(f'Changed source: {p}')
for p,r in ev['references'].items():
    if sha(study/'reference'/p)!=r['sha256']: raise ValueError(f'Changed reference: {p}')
f=json.loads((data/'docs/development/battlefields/cold-mires/dataset/features.json').read_text())
r=np.load(data/'docs/development/battlefields/cold-mires/dataset/rasters.npz')
manifest=json.loads((data/'docs/development/battlefields/cold-mires/dataset/manifest.json').read_text())
setup=Image.open(study/'reference/game-setup-original.png')
battle=Image.open(study/'reference/game-battlefield-original.png')
preview=setup.crop(ev['alignment']['preview_crop_xyxy_half_open'])
out=study/'figures';out.mkdir(exist_ok=True)
# Only presentation is reversed: source samples remain unchanged. The origin is
# the minimum native Z at the bottom, matching the observed game-preview frame.
x=1088+np.arange(r['ground'].shape[1])*2
z=1536+np.arange(r['ground'].shape[0])*2
extent=(1087,2047,1535,2559)
colors={1:'#116a8d',8:'#116a8d',18:'#116a8d',3:'#b42d86',21:'#b42d86',26:'#b42d86'}
polys={int(p['id'].split('-')[-1]):np.array(p['points_xz']) for p in f['blockage_candidates']}
def label(ax,k,xy=None):
    p=polys[k]; pos=p.mean(axis=0) if xy is None else xy
    ax.text(*pos,str(k),ha='center',va='center',fontsize=12,weight='bold',color='white',zorder=12,
            bbox=dict(boxstyle='round,pad=.18',fc=colors[k],ec='white',lw=.7))
def axes_frame(ax):
    ax.set(xlim=(1088,2048),ylim=(1536,2560),aspect='equal')
    ax.set_xticks([]);ax.set_yticks([])
def source(ax):
    earth=LinearSegmentedColormap.from_list('earth',['#eee9d8','#d8c9aa','#b29876','#82674f'])
    ax.imshow(r['ground'],origin='lower',extent=extent,cmap=earth,vmin=60,vmax=150)
    ax.contour(x,z,r['ground'],levels=[70,90,110,130],colors='#8b7961',linewidths=.5,alpha=.7)
    # Keep water classes identical to the pinned classifier; import only after hash checks.
    import importlib.util
    spec=importlib.util.spec_from_file_location('cold_mires_classifier',data/'scripts/cold_mires/classify_water.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    lab,_=module.classify(r['ground'],r['water'],r['raw_water'],**manifest['water_classifier']['parameters'])
    for val,col in [(0,'#ccc9c2'),(2,'#b5dbdf'),(3,'#68bbd0'),(4,'#eab448'),(5,'#253f86')]:
        ax.imshow(np.ma.masked_where(lab!=val,np.ones(lab.shape)),origin='lower',extent=extent,cmap=ListedColormap([col]),interpolation='nearest',alpha=.9)
    for k,p in polys.items():
        ax.add_patch(Polygon(p,closed=True,facecolor='#b56752',edgecolor='#97513e',alpha=.5,lw=.65))
    for kind,col,size in [('spruce','#174e30',4),('reed','#a46b21',1.3)]:
        pts=np.array([v['xyz'] for v in f['vegetation'] if kind in v['model'].lower()])
        ax.scatter(pts[:,0],pts[:,2],s=size,c=col,lw=0,zorder=5)
    for k in colors: label(ax,k)
    axes_frame(ax)
def game(ax,overlay=True):
    ax.imshow(preview,extent=(1088,2048,1536,2560),interpolation='nearest')
    if overlay:
        for k,p in polys.items():
            if k in colors: ax.add_patch(Polygon(p,fill=False,edgecolor='#79f8f3',lw=1.8,zorder=6))
        for k in colors:label(ax,k)
    axes_frame(ax)
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11})
fig=plt.figure(figsize=(16,19),facecolor='#f7f4ec')
fig.text(.055,.968,'COLD MIRES  /  GEOGRAPHIC CHECKPOINT',fontsize=24,weight='bold',color='#162b38')
fig.text(.055,.943,'Partial support: distinctive rock geometry corresponds; water boundaries and runtime selectors remain unresolved.',fontsize=12)
left=fig.add_axes([.055,.50,.42,.405]);right=fig.add_axes([.525,.50,.42,.405])
source(left);game(right)
left.set_title('A  Retained hypothesis · display flipped vertically',loc='left',fontsize=13,pad=13)
right.set_title('B  Actual game setup preview · source outlines overlaid',loc='left',fontsize=13,pad=13)
fig.text(.055,.474,'Same full crop; X unchanged, Z increases upward here. No local fitting.\nOriginal Data rendering is preserved separately, with Z increasing downward.',fontsize=10,color='#454d50')
fig.text(.525,.474,'Preview enlarged from 274 × 293 pixels; cyan = source outline.\n1 selected the orientation; 8 / 18 and 3 / 21 / 26 are additional checks.',fontsize=10,color='#454d50')
fig.text(.525,.462,'U1  Water footprint / depth: not verifiable from this preview.',fontsize=10,color='#9a5600',weight='bold')
ax=fig.add_axes([.055,.137,.89,.305]);ax.imshow(battle);ax.set_xlim(0,1715);ax.set_ylim(930,65);ax.axis('off')
ax.set_title('C  Actual battlefield · perspective observations (not a measured projection)',loc='left',fontsize=13,pad=10)
for txt,pt,where in [('Curved rocky rim\nCandidate outline 1',(160,509),(50,190)),('Wooded elongated rise',(610,438),(505,110)),('Isolated rock formation\nCandidate outline 18',(1125,582),(1040,765)),('Steep rocky background',(1470,150),(1205,370))]:
    ax.annotate(txt,xy=pt,xytext=where,color='white',fontsize=11,ha='left',va='center',
        bbox=dict(boxstyle='round,pad=.35',fc='#162b38',alpha=.9,ec='white',lw=.6),
        arrowprops=dict(arrowstyle='->',lw=1.5,color='white'))
fig.text(.055,.115,'Live-view identifications are tentative. Its minimap repeats the setup layout; no camera/world calibration was fitted.',fontsize=10,color='#454d50')
handles=[Patch(color='#68bbd0',label='Conditional shallow water'),Patch(color='#253f86',label='Conditional deep water'),Patch(color='#eab448',label='Uncertain depth boundary'),Patch(color='#ccc9c2',label='Unknown water coverage'),Line2D([],[],marker='o',ls='',color='#174e30',label='Spruce points (not canopy)')]
fig.legend(handles=handles,loc='lower left',bbox_to_anchor=(.05,.073),ncol=3,frameon=False,fontsize=10)
fig.text(.055,.04,'Data: 9.0.1 / Steam 25546563 · 3b5d13d94c5e. Game: user reports “9.0.3”; exact build unverified.\nWater cutoff = conditional 0.5 native units; uncertainty covers quantization only. No passability, concealment or projectile claim.\nAnalysis stewardship · 4 October 2026 · Full originals, hashes, fixed transform and reproduction commands accompany this figure.',fontsize=10,color='#454d50',linespacing=1.5)
fig.savefig(out/'cold-mires-annotated-comparison.png',dpi=180,facecolor=fig.get_facecolor());plt.close(fig)
# Separate raw preview and overlay for inspecting exact silhouette agreement.
fig,axes=plt.subplots(1,2,figsize=(12,7),facecolor='#f7f4ec')
game(axes[0],False);game(axes[1],True)
axes[0].set_title('Game preview crop · unchanged pixels',fontsize=13)
axes[1].set_title('Same crop + fixed source outlines',fontsize=13)
fig.suptitle('Cold Mires · no local warping or individual landmark shifts',fontsize=16)
fig.text(.08,.02,'Visual overlap supports relative rock geometry, not certified collision footprints or metre-scale coordinates.',fontsize=10)
fig.subplots_adjust(top=.89,bottom=.08,wspace=.08)
fig.savefig(out/'cold-mires-preview-overlay.png',dpi=180,facecolor=fig.get_facecolor());plt.close(fig)
print(json.dumps({'status':'passed','data_commit':head,'source_hashes_checked':len(lock['files']),'reference_hashes_checked':len(ev['references']),'figures':[p.name for p in sorted(out.glob('*.png'))]}))
