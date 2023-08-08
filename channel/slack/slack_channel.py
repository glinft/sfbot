# encoding:utf-8

import re
import json
from flask import Flask, request
from flask_cors import CORS
from common import const
from common import log
from common.redis import RedisSingleton
from config import common_conf_val, channel_conf
from channel.channel import Channel
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler

app = App(
    token=channel_conf(const.SLACK).get('slack_bot_token'),
    signing_secret=channel_conf(const.SLACK).get('slack_signing_secret')
)
authresp = app.client.auth_test()
bot_user_id = authresp["user_id"]

@app.middleware
def log_request(payload, next):
    log.info(payload)
    return next()

@app.event("app_mention")
def handle_mention(event, say):
    msg_id = event["client_msg_id"]
    event_type = event["type"] # app_mention
    log.info(f"## Slack Mention: {event_type}/{msg_id}")
    pass

@app.event("message")
def handle_message(event, say):
    msg_id = event["client_msg_id"]
    event_type = event["type"] # message
    log.info(f"## Slack Message: {event_type}/{msg_id}")
    event_ts = event["event_ts"]
    ts = event["ts"]
    if 'thread_ts' in event:
        ts = event["thread_ts"]
    channel_type = event["channel_type"] # im/channel
    channel = event["channel"]
    team = event["team"]
    user = event["user"]
    text = event["text"]
    if channel_type=="im":
        channel_type = "private"
    elif channel_type=="channel":
        mentionuser = "<@{}>".format(bot_user_id)
        if mentionuser not in text:
            return
    else:
        return
    reply_text = SlackChannel().handle(event)
    splits=reply_text.split("```sf-json")
    if len(splits)==2:
        extra=json.loads(splits[1][1:-4])
        reply_text=splits[0]
        pages=extra.get('pages',[])
        if len(pages)>0:
            reply_text+="\n"
            for page in pages:
                reply_text+="\n<{}|{}>".format(page['url'],page['title'])
    log.info('[Slack] reply content: {}'.format(reply_text))
    say(text=f"{reply_text}", thread_ts=ts)

@app.command("/help")
def handle_help(ack, respond, command):
    ack()
    markdown_message = "Hi, I am a chatbot _powered by_ <https://www.easiio.com/|Easiio>. What can I do for you?"
    respond(markdown_message)

@app.command("/reset")
def handle_reset(ack, respond, command):
    ack()
    markdown_message = "*Conversation history forgotten.*"
    respond(markdown_message)

@app.command("/getid")
def handle_getid(ack, respond, command):
    ack()
    channel_id = command["channel_id"]
    channel_name = command["channel_name"]
    user_id = command["user_id"]
    user_name = command["user_name"]
    if channel_name=="directmessage":
        markdown_message = "*User ID*: {}\n*Username*: @{}".format(user_id, user_name)
    else:
        markdown_message = "*Channel ID*: {}\n*Channel Title*: #{}".format(channel_id, channel_name)
    respond(markdown_message)

flask_app = Flask(__name__)
CORS(flask_app)
handler = SlackRequestHandler(app)

@flask_app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Origin, X-Requested-With, Content-Type, Accept, Authorization'
    return response


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/slack/commands", methods=["POST"])
def slack_commands():
    return handler.handle(request)

@flask_app.route("/slack/interactions", methods=["POST"])
def slack_interactions():
    return handler.handle(request)

class SlackChannel(Channel):
    def startup(self):
        flask_app.run(host='0.0.0.0', port=channel_conf(const.SLACK).get('port'))

    def handle(self, event):
        context = dict()
        channel = event["channel"]
        channel_type = event["channel_type"]
        user = event["user"]
        text = event["text"]
        context['from_user_id'] = "{}:{}".format(channel,user)
        context['from_org_id'] = "org:4:bot:9"
        routekey="slack:channel:"+channel
        if channel_type=="im":
            routekey="slack:private:"+user
        myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
        orgbot = myredis.redis.hget('sfbot:route', routekey)
        if orgbot is not None:
            context['from_org_id'] = orgbot.decode()
        context['res'] = "0"
        context['userflag'] = "external"
        context['character_desc'] = "undef"
        context['temperature'] = "undef"
        plain_text = re.sub(r"<@\w+>", "", text)
        return super().build_reply_content(plain_text, context)
