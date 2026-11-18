import io,copy,math
import numpy as np
from PIL import Image,ImageDraw
from image_lab.validation import Invalid

def integer(v,lo,hi,name):
    if isinstance(v,bool):raise Invalid(name+'需要整数')
    try:n=int(v)
    except (TypeError,ValueError):raise Invalid(name+'需要整数')
    if n!=float(v) or not lo<=n<=hi:raise Invalid(name+'超出范围')
    return n

def png(a):
    out=io.BytesIO();Image.fromarray(np.asarray(a,dtype=np.uint8),'RGBA').save(out,format='PNG');return out.getvalue()

def decode(raw):
    if len(raw)>12*1024*1024:raise Invalid('文件超过12MB')
    try:
        im=Image.open(io.BytesIO(raw));im.load()
        if max(im.size)>2048:raise Invalid('图片最长边不能超过2048')
        return np.array(im.convert('RGBA'))
    except Invalid:raise
    except Exception:raise Invalid('无法读取图片')

def demo():
    a=Image.new('RGBA',(160,100),'#faf0dc');d=ImageDraw.Draw(a);d.rectangle((5,5,154,94),outline='#183c43',width=4);d.ellipse((20,15,83,78),fill='#e8845a');d.polygon([(95,20),(145,80),(85,80)],fill='#387d8b');d.text((10,83),'CUTBOARD 01',fill='#183c43');return np.array(a)

def create(name,w,h):
    return dict(name=str(name)[:100],width=integer(w,64,1024,'纸宽'),height=integer(h,64,1024,'纸高'),gap=4,background='#ffffff',pieces=[],undo=[],redo=[])

def snapshot(p):return {k:copy.deepcopy(v) for k,v in p.items() if k not in ('id','revision','source','undo','redo')}

def checkpoint(p):p['undo']=(p.get('undo',[])+[snapshot(p)])[-40:];p['redo']=[]

def validate(p):
    if not isinstance(p,dict):raise Invalid('排版需为对象')
    integer(p.get('width'),64,1024,'纸宽');integer(p.get('height'),64,1024,'纸高');integer(p.get('gap',4),0,32,'间隔')
    if not isinstance(p.get('pieces'),list) or len(p['pieces'])>40:raise Invalid('每个排版最多40个裁片')
    if not isinstance(p.get('background'),str) or len(p['background'])!=7 or p['background'][0]!='#':raise Invalid('背景色无效')
    try:int(p['background'][1:],16)
    except ValueError:raise Invalid('背景色无效')
    ids=set()
    for q in p['pieces']:
        if q.get('id') in ids:raise Invalid('重复裁片ID')
        ids.add(q.get('id'));integer(q.get('width'),1,1024,'裁片宽');integer(q.get('height'),1,1024,'裁片高');integer(q.get('page'),1,20,'页号');integer(q.get('x'),0,1024,'横坐标');integer(q.get('y'),0,1024,'纵坐标')
        if q.get('rotation') not in (0,90):raise Invalid('仅支持0或90度旋转')
        if not isinstance(q.get('image_id'),str):raise Invalid('裁片缺少原图')
        c=q.get('crop');
        if not isinstance(c,list) or len(c)!=4 or not all(isinstance(x,int) for x in c) or not(0<=c[0]<c[2] and 0<=c[1]<c[3]):raise Invalid('裁片裁剪范围无效')
    return p

def extent(q):return (q['height'],q['width']) if q['rotation']==90 else (q['width'],q['height'])

def issues(p):
    out=[];gap=p['gap']
    for i,q in enumerate(p['pieces']):
        w,h=extent(q)
        if q['x']+w>p['width'] or q['y']+h>p['height']:out.append(dict(kind='outside',piece=q['id']))
        for r in p['pieces'][:i]:
            if q['page']!=r['page']:continue
            rw,rh=extent(r)
            if not(q['x']+w+gap<=r['x'] or r['x']+rw+gap<=q['x'] or q['y']+h+gap<=r['y'] or r['y']+rh+gap<=q['y']):out.append(dict(kind='overlap_or_gap',pieces=[r['id'],q['id']]))
    return out

def summary(p):
    pages=sorted({q['page'] for q in p['pieces']});area=sum(q['width']*q['height'] for q in p['pieces']);total=len(pages)*p['width']*p['height']
    return dict(count=len(p['pieces']),pages=pages,used_area=area,utilization=round(area/total,4) if total else 0,groups=sorted({q['group'] for q in p['pieces']}),issues=issues(p))

def edit(p,b,image=None):
    action=b.get('action');checkpoint(p)
    if action=='add':
        if image is None:raise Invalid('缺少原图')
        row,a=image;import uuid
        p['pieces'].append(dict(id=uuid.uuid4().hex,image_id=row['id'],name=str(b.get('name',row['name']))[:80],group=str(b.get('group','默认'))[:40],width=integer(b.get('width',80),1,1024,'宽'),height=integer(b.get('height',50),1,1024,'高'),x=0,y=0,page=1,rotation=0,allow_rotate=True,crop=[0,0,a.shape[1],a.shape[0]]))
    elif action=='sheet':
        for field,lo,hi in [('width',64,1024),('height',64,1024),('gap',0,32)]:
            if field in b:p[field]=integer(b[field],lo,hi,field)
        if 'background' in b:p['background']=str(b['background'])
        if 'name' in b:p['name']=str(b['name'])[:100]
    elif action in ('piece','delete','duplicate','crop','trim'):
        q=next((q for q in p['pieces'] if q['id']==b.get('piece_id')),None)
        if q is None:raise Invalid('请选择裁片')
        if action=='delete':p['pieces'].remove(q)
        elif action=='duplicate':
            import uuid
            v=copy.deepcopy(q);v['id']=uuid.uuid4().hex;v['name']+=' 副本';p['pieces'].append(v)
        elif action in ('crop','trim'):
            if image is None:raise Invalid('缺少原图')
            a=image[1]
            if action=='trim':
                yy,xx=np.nonzero(a[:,:,3]>0)
                if not len(xx):raise Invalid('全透明原图无法自动裁边')
                q['crop']=[int(xx.min()),int(yy.min()),int(xx.max()+1),int(yy.max()+1)]
            else:
                c=b.get('crop');
                if not isinstance(c,list) or len(c)!=4:raise Invalid('需要左上右下四个整数')
                q['crop']=[integer(c[0],0,a.shape[1]-1,'左'),integer(c[1],0,a.shape[0]-1,'上'),integer(c[2],1,a.shape[1],'右'),integer(c[3],1,a.shape[0],'下')]
        else:
            for field,lo,hi in [('width',1,1024),('height',1,1024),('x',0,1024),('y',0,1024),('page',1,20)]:
                if field in b:q[field]=integer(b[field],lo,hi,field)
            for field in ('name','group'):
                if field in b:q[field]=str(b[field])[:80]
            if 'rotation' in b:q['rotation']=integer(b['rotation'],0,90,'旋转')
            if 'allow_rotate' in b:q['allow_rotate']=bool(b['allow_rotate'])
    else:raise Invalid('未知编辑动作')
    validate(p)

def history(p,action):
    if action not in ('undo','redo'):raise Invalid('未知历史动作')
    other='redo' if action=='undo' else 'undo'
    if not p.get(action):raise Invalid('没有可用历史')
    old=p[action].pop();p.setdefault(other,[]).append(snapshot(p))
    for k in list(p):
        if k not in ('id','revision','source','undo','redo'):p.pop(k)
    p.update(old);validate(p)

def render(p,images,page=1,guides=False):
    canvas=Image.new('RGBA',(p['width'],p['height']),p['background']);draw=ImageDraw.Draw(canvas)
    for q in p['pieces']:
        if q['page']!=page:continue
        a=images[q['image_id']];c=q['crop'];im=Image.fromarray(a,'RGBA').crop(c).resize((q['width'],q['height']),Image.Resampling.LANCZOS)
        if q['rotation']==90:im=im.transpose(Image.Transpose.ROTATE_270)
        canvas.alpha_composite(im,(q['x'],q['y']))
        if guides:
            w,h=extent(q);draw.rectangle((q['x'],q['y'],q['x']+w-1,q['y']+h-1),outline='#d34848',width=1);draw.text((q['x']+3,q['y']+3),q['name'][:16],fill='#153c46')
    return np.array(canvas)
