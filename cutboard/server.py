import io,copy
from flask import request,jsonify,render_template,send_file
from image_lab.validation import Invalid,Conflict
from .storage import Storage
from .core import decode,demo,create,edit,history,summary,integer,png
from .autolayout import build_plan,render_plan,verify_plan

def install(app,root):
    store=Storage(root);app.extensions['cutboard']=store
    def body():
        b=request.get_json(silent=True)
        if not isinstance(b,dict):raise Invalid('请求需为JSON对象')
        return b
    def result(p):return jsonify(layout=p,summary=summary(p))
    @app.get('/')
    def page():return render_template('cutboard.html')
    @app.get('/cutboard/state')
    def state():return jsonify(store.state())
    @app.post('/cutboard/demo')
    def sample():return jsonify(image=store.save_image('合成卡片',demo()))
    @app.post('/cutboard/upload')
    def upload():
        f=request.files.get('file')
        if not f:raise Invalid('请选择图片')
        return jsonify(image=store.save_image(f.filename,decode(f.read())))
    @app.get('/cutboard/images/<key>.png')
    def image(key):return send_file(io.BytesIO(png(store.image(key)[1])),mimetype='image/png')
    @app.post('/cutboard/layouts')
    def new():
        b=body();return result(store.save(create(b.get('name','新排版'),b.get('width',320),b.get('height',240)),dict(manual=True)))
    @app.get('/cutboard/layouts/<key>')
    def get(key):return result(store.layout(key))
    @app.post('/cutboard/layouts/<key>/edit')
    def change(key):
        b=body();im=None
        if b.get('action')=='add':im=store.image(b.get('image_id'))
        elif b.get('action') in ('crop','trim'):
            p=store.layout(key);q=next((q for q in p['pieces'] if q['id']==b.get('piece_id')),None)
            if not q:raise Invalid('请选择裁片')
            im=store.image(q['image_id'])
        return result(store.change(key,b.get('revision'),lambda p:edit(p,b,im)))
    @app.post('/cutboard/layouts/<key>/history')
    def hist(key):
        b=body();return result(store.change(key,b.get('revision'),lambda p:history(p,b.get('action'))))
    @app.post('/cutboard/layouts/<key>/auto-layout')
    def auto_layout(key):
        b=body();mode=b.get('mode')
        if mode not in ('save','group'):raise Invalid('请选择省纸或整组排版方式')
        p=store.layout(key)
        if b.get('revision')!=p['revision']:raise Conflict('排版已改变，请刷新')
        plan=build_plan(p,mode)
        return jsonify(plan=plan,summary=summary(p))
    @app.post('/cutboard/layouts/<key>/auto-layout/preview.png')
    def auto_layout_preview(key):
        b=body();plan=b.get('plan');page=integer(b.get('page',1),1,20,'页号')
        if not isinstance(plan,dict):raise Invalid('缺少排版方案')
        p=store.layout(key)
        verify_plan(p,plan)
        if not plan.get('pages'):raise Invalid('该方案没有可预览的页面（存在放不下的分组）')
        if not any(v['page']==page for v in plan['pages']):raise Invalid('预览页号超出方案页数')
        images={q['image_id']:store.image(q['image_id'])[1] for q in p['pieces']}
        return send_file(io.BytesIO(png(render_plan(p,images,plan,page))),mimetype='image/png')
    @app.post('/cutboard/layouts/<key>/auto-layout/apply')
    def auto_layout_apply(key):
        b=body();plan=b.get('plan')
        if not isinstance(plan,dict):raise Invalid('缺少排版方案')
        return result(store.change(key,b.get('revision'),lambda p:edit(p,dict(action='autolayout',plan=plan))))
    @app.post('/cutboard/layouts/<key>/duplicate')
    def duplicate(key):
        p=store.layout(key);p['name']+=' 副本';p['undo']=[];p['redo']=[];return result(store.save(p,dict(layout_id=key,revision=p['revision'])))
    @app.post('/cutboard/import')
    def restore():return result(store.save(copy.deepcopy(body().get('layout')),dict(imported=True)))
    @app.get('/cutboard/layouts/<key>.png')
    def image_export(key):return send_file(io.BytesIO(store.render(key,integer(request.args.get('page',1),1,20,'页号'),request.args.get('guides')=='1')),mimetype='image/png',download_name='cutboard.png',as_attachment=request.args.get('download')=='1')
    @app.get('/cutboard/layouts/<key>.zip')
    def archive(key):return send_file(io.BytesIO(store.export(key)),mimetype='application/zip',download_name='cutboard.zip',as_attachment=True)
