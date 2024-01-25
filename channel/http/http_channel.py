# encoding:utf-8

import asyncio
import json
from channel.http import auth
from flask import Flask, request, render_template, make_response
from datetime import timedelta
from common import const
from common import functions
from config import channel_conf, channel_conf_val
from channel.channel import Channel
from flask_socketio import SocketIO
from flask_cors import CORS
from common import log
from plugins.plugin_manager import *

http_app = Flask(__name__,)
socketio = SocketIO(http_app, path='/sfbot/socket.io', cors_allowed_origins=['https://api.sflow.io'], close_timeout=5)
CORS(http_app) # supports_credentials=True

@http_app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Origin, X-Requested-With, Content-Type, Accept, Authorization'
    return response

# 自动重载模板文件
http_app.jinja_env.auto_reload = True
http_app.config['TEMPLATES_AUTO_RELOAD'] = True

# 设置静态文件缓存过期时间
http_app.config['SEND_FILE_MAX_AGE_DEFAULT'] = timedelta(seconds=1)


async def return_stream(data):
    async for final, response in HttpChannel().handle_stream(data=data):
        try:
            extra={}
            splits=response.split("```sf-json")
            if len(splits)==2:
                extra=json.loads(splits[1][1:-4])
                response=splits[0]
            if (final):
                socketio.server.emit(
                    'disconnect', {
                        'result': response,
                        'docs': extra.get('docs',[]),
                        'pages': extra.get('pages',[]),
                        'resources': extra.get('resources',[]),
                        'commands': extra.get('commands',[]),
                        'teammode': extra.get('teammode',None),
                        'teamid': extra.get('teamid',None),
                        'teambotid': extra.get('teambotid',None),
                        'logid': extra.get('logid',None),
                        'final': final,
                    }, request.sid, namespace="/sfbot/chat")
                disconnect()
            else:
                socketio.server.emit(
                    'message', {
                        'result': response,
                        'docs': extra.get('docs',[]),
                        'pages': extra.get('pages',[]),
                        'resources': extra.get('resources',[]),
                        'commands': extra.get('commands',[]),
                        'teammode': extra.get('teammode',None),
                        'teamid': extra.get('teamid',None),
                        'teambotid': extra.get('teambotid',None),
                        'logid': extra.get('logid',None),
                        'final': final,
                    }, request.sid, namespace="/sfbot/chat")
        except Exception as e:
            disconnect()
            log.warn("[http]emit:{}", e)
            break


@socketio.on('message', namespace='/sfbot/chat')
def stream(data):
    flag, orgid = auth.identify(request)
    if (flag == False):
        client_sid = request.sid
        socketio.server.disconnect(client_sid)
        return
    data = json.loads(data["data"])
    if (data):
        data['orgid'] = orgid
        img_match_prefix = functions.check_prefix(
            data["msg"], channel_conf_val(const.HTTP, 'image_create_prefix'))
        if img_match_prefix:
            reply_text = HttpChannel().handle(data=data)
            socketio.emit(
                'disconnect', {'result': reply_text}, namespace='/sfbot/chat')
            disconnect()
            return
        asyncio.run(return_stream(data))


@socketio.on('connect', namespace='/sfbot/chat')
def connect():
    log.info('connected')
    socketio.emit('message', {'info': "connected"}, namespace='/sfbot/chat')


@socketio.on('disconnect', namespace='/sfbot/chat')
def disconnect():
    log.info('disconnect')
    socketio.server.disconnect(request.sid, namespace="/sfbot/chat")


@http_app.route("/sfbot/chat", methods=['POST'])
def chat():
    flag, orgid = auth.identify(request)
    if (flag == False):
        return {'error':'auth failed'}
    data = json.loads(request.data)
    if data:
        msg = data.get("msg")
        if not msg:
            return {'error':'no message'}
        data['orgid'] = orgid
        reply_text = HttpChannel().handle(data=data)
        extra={}
        splits=reply_text.split("```sf-json")
        if len(splits)==2:
            extra=json.loads(splits[1][1:-4])
            reply_text=splits[0]
        return {'result': reply_text,
                'docs': extra.get('docs',[]),
                'pages': extra.get('pages',[]),
                'resources': extra.get('resources',[]),
                'commands': extra.get('commands',[]),
                'score': extra.get('score',None),
                'teammode': extra.get('teammode',None),
                'teamid': extra.get('teamid',None),
                'teambotid': extra.get('teambotid',None),
                'logid': extra.get('logid',None)}


@http_app.route("/sfbot/sms", methods=['POST'])
def handle_sms():
    flag, orgid = auth.check_apikey(request)
    if (flag == False):
        return {'error':'auth failed'}
    if not orgid:
        return {'error':'no chatbot setting'}
    log.info('SMS: '+request.data.decode('utf-8'))
    data = json.loads(request.data)
    if data:
        msg = data.get("message")
        if not msg:
            return {'error':'no message'}
        mfr = data.get("from")
        if not mfr:
            return {'error':'no originator'}
        data['msg'] = str(msg)
        data['message'] = None
        data['id'] = str(mfr)
        data['orgid'] = orgid
        data['res'] = '0'
        data['userflag'] = 'external'
        data['sfmodel'] = 'undef'
        data['character_desc'] = 'undef'
        data['temperature'] = 'undef'
        reply_text = HttpChannel().handle(data=data)
        splits=reply_text.split("```sf-json")
        if len(splits)==2:
            reply_text=splits[0]
        return {'result': reply_text}


@http_app.route("/sfbot", methods=['GET'])
def index():
    flag, orgid = auth.identify(request)
    if (flag == False):
        return login()
    return render_template('index.html')


@http_app.route("/sfbot/static/<path:filename>")
def serve_css(filename):
    return http_app.send_static_file(filename)


@http_app.route("/sfbot/login", methods=['POST', 'GET'])
def login():
    response = make_response("<html></html>", 301)
    response.headers.add_header('content-type', 'text/plain')
    response.headers.add_header('location', '/sfbot')
    flag, orgid = auth.identify(request)
    if (flag == True):
        return response
    else:
        if request.method == "POST":
            token = auth.authenticate(request.form['username'], request.form['password'])
            if (token != False):
                response.set_cookie(key='Authorization', value=token)
                return response
        else:
            return render_template('login.html')
    response.headers.set('location', '/sfbot/login?err=登录失败')
    return response


@http_app.route("/sfbot/login2", methods=['POST'])
def login2():
    response = make_response("<html></html>", 200)
    response.headers.add_header('content-type', 'text/plain')
    flag, orgid = auth.identify(request)
    if (flag == True):
        return response
    else:
        if request.method == "POST":
            token = auth.authenticate(request.form['username'], request.form['password'])
            if (token != False):
                response = make_response(token, 200)
                response.set_cookie(key='Authorization', value=token)
                return response
    response = make_response("<html></html>", 400)
    response.headers.add_header('content-type', 'text/plain')
    return response


class HttpChannel(Channel):
    def startup(self):
        http_app.run(host='0.0.0.0', port=channel_conf(const.HTTP).get('port'))

    def handle(self, data):
        context = dict()
        query = data["msg"]
        id = data["id"]
        context['from_user_id'] = str(id)
        orgid = data["orgid"]
        context['from_org_id'] = str(orgid)
        res = data.get("res", 0)
        context['res'] = str(res)
        fwd = data.get("fwd", 0)
        context['fwd'] = str(fwd)
        userflag = data.get("userflag", 'external')
        context['userflag'] = str(userflag)
        sfmodel = data.get("model", 'undef')
        context['sfmodel'] = str(sfmodel)
        character_id = data.get("character_id")
        if character_id is not None:
            context['character_id'] = 'c'+str(character_id)
        character_desc = data.get("character_desc", 'undef')
        context['character_desc'] = str(character_desc)
        temperature = data.get("temperature", 'undef')
        context['temperature'] = str(temperature)
        website= data.get("website", 'undef')
        context['website'] = str(website)
        email= data.get("email", 'undef')
        context['email'] = str(email)
        teammode = data.get("teammode", 0)
        context['teammode'] = str(teammode)
        teambotkeep = data.get("teambotkeep", 0)
        context['teambotkeep'] = str(teambotkeep)
        teamid = data.get("teamid", 0)
        context['teamid'] = str(teamid)
        teambotid = data.get("teambotid", 0)
        context['teambotid'] = str(teambotid)
        userid = data.get("userid", 'undef')
        context['userid'] = str(userid)
        userasst = data.get("userasst", 'undef')
        context['userasst'] = str(userasst)
        e_context = PluginManager().emit_event(EventContext(Event.ON_HANDLE_CONTEXT, {
            'channel': self, 'context': query,  "args": context}))
        reply = e_context['reply']
        if not e_context.is_pass():
            reply = super().build_reply_content(e_context["context"], e_context["args"])
            e_context = PluginManager().emit_event(EventContext(Event.ON_DECORATE_REPLY, {
                'channel': self, 'context': context, 'reply': reply, "args": context}))
            reply = e_context['reply']
        return reply

    async def handle_stream(self, data):
        context = dict()
        query = data["msg"]
        id = data["id"]
        context['from_user_id'] = str(id)
        orgid = data["orgid"]
        context['from_org_id'] = str(orgid)
        res = data.get("res", 0)
        context['res'] = str(res)
        fwd = data.get("fwd", 0)
        context['fwd'] = str(fwd)
        userflag = data.get("userflag", 'external')
        context['userflag'] = str(userflag)
        sfmodel = data.get("model", 'undef')
        context['sfmodel'] = str(sfmodel)
        character_id = data.get("character_id")
        if character_id is not None:
            context['character_id'] = 'c'+str(character_id)
        character_desc = data.get("character_desc", 'undef')
        context['character_desc'] = str(character_desc)
        temperature = data.get("temperature", 'undef')
        context['temperature'] = str(temperature)
        website= data.get("website", 'undef')
        context['website'] = str(website)
        email= data.get("email", 'undef')
        context['email'] = str(email)
        teammode = data.get("teammode", 0)
        context['teammode'] = str(teammode)
        teambotkeep = data.get("teambotkeep", 0)
        context['teambotkeep'] = str(teambotkeep)
        teamid = data.get("teamid", 0)
        context['teamid'] = str(teamid)
        teambotid = data.get("teambotid", 0)
        context['teambotid'] = str(teambotid)
        userid = data.get("userid", 'undef')
        context['userid'] = str(userid)
        userasst = data.get("userasst", 'undef')
        context['userasst'] = str(userasst)
        context['stream'] = True
        context['origin'] = query
        e_context = PluginManager().emit_event(EventContext(Event.ON_HANDLE_CONTEXT, {
            'channel': self, 'context': query, 'reply': query, "args": context}))
        reply = e_context['reply']
        if not e_context.is_pass():
            async for final, reply in super().build_reply_stream(query, context):
                yield final, reply
        else:
            yield True, reply
