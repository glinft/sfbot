# encoding:utf-8

import os
import re
import json
import html
from flask import Flask, request, make_response
from flask_cors import CORS
from common import const
from common import log
from common.redis import RedisSingleton
from config import common_conf_val, channel_conf
from channel.channel import Channel
from model.openai.chatgpt_model import Session
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from slack_bolt.oauth.oauth_settings import OAuthSettings
from slack_sdk.oauth import AuthorizeUrlGenerator
from slack_sdk.oauth.installation_store import FileInstallationStore, Installation
from slack_sdk.oauth.state_store import FileOAuthStateStore
from slack_sdk.web import WebClient
from slack.errors import SlackApiError

client_id = channel_conf(const.SLACK).get('slack_client_id')
client_secret = channel_conf(const.SLACK).get('slack_client_secret')
signing_secret = channel_conf(const.SLACK).get('slack_signing_secret')
state_store = FileOAuthStateStore(expiration_seconds=86400, base_dir="/opt/boltstore/states")
installation_store = FileInstallationStore(base_dir="/opt/boltstore/installations")
authorize_url_generator = AuthorizeUrlGenerator(
    client_id=client_id,
    scopes=["app_mentions:read", "channels:history", "channels:read", "chat:write", "commands", "files:write", "groups:history", "groups:read", "im:history", "im:read", "mpim:history", "mpim:read", "team.billing:read", "team:read", "users:read"],
    user_scopes=[],
)

"""
app = App(
    signing_secret=signing_secret,
    token=channel_conf(const.SLACK).get('slack_bot_token'),
)
# Callback to run on successful installation
def success(args: SuccessArgs) -> BoltResponse:
    # Call default handler to return an HTTP response
    return args.default.success(args)
    # return BoltResponse(status=200, body="Installation successful!")


# Callback to run on failed installation
def failure(args: FailureArgs) -> BoltResponse:
    return args.default.failure(args)
    # return BoltResponse(status=args.suggested_status_code, body=args.reason)
"""

app = App(
    signing_secret=signing_secret,
    installation_store=installation_store,
    oauth_settings=OAuthSettings(
        client_id=client_id,
        client_secret=client_secret,
        scopes=["app_mentions:read", "channels:history", "channels:read", "chat:write", "commands", "files:write", "groups:history", "groups:read", "im:history", "im:read", "mpim:history", "mpim:read", "team.billing:read", "team:read", "users:read"],
        user_scopes=[],
        redirect_uri=None,
        install_path="/slack/install",
        redirect_uri_path="/slack/oauth_redirect",
        state_store=state_store,
        # callback_options=CallbackOptions(success=success, failure=failure),
    ),
)
# authresp = app.client.auth_test()
# bot_user_id = authresp["user_id"]

@app.middleware
def log_request(payload, next):
    log.info(payload)
    return next()

@app.event("app_home_opened")
def handle_home_opened(event):
    event_type = event["type"]
    channel = event["channel"]
    user = event["user"]
    tab = event["tab"]
    event_ts = event["event_ts"]
    log.info(f"## Slack Event: {event_type}/{channel}/{user}/{tab}/{event_ts}")
    pass

@app.event("app_mention")
def handle_mention(event, say):
    event_type = event["type"]
    msg_id = event.get("client_msg_id","n/a")
    log.info(f"## Slack Event: {event_type}/{msg_id}")
    event_ts = event["event_ts"]
    ts = event["ts"]
    if 'thread_ts' in event:
        ts = event["thread_ts"]
    channel = event["channel"]
    team = event.get("team","n/a")
    user = event["user"]
    text = event["text"]
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
    say(text=reply_text, thread_ts=ts)

@app.event("message")
def handle_message(event, say):
    event_type = event["type"]
    msg_id = event.get("client_msg_id","n/a")
    log.info(f"## Slack Message: {event_type}/{msg_id}")
    event_ts = event["event_ts"]
    ts = event["ts"]
    if 'thread_ts' in event:
        ts = event["thread_ts"]
    channel_type = event.get("channel_type","n/a") # im/channel
    channel = event["channel"]
    team = event.get("team","n/a")
    user = event["user"]
    text = event["text"]
    if channel_type=="im":
        channel_type = "private"
    # elif channel_type=="channel": mentionuser = "<@{}>".format(bot_user_id) if mentionuser not in text: return
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
    say(text=reply_text, thread_ts=ts)

@app.command("/help")
def handle_help(ack, respond, command):
    ack()
    markdown_message = "Hi, I am a chatbot _powered by_ <https://www.easiio.com/|Easiio>. What can I do for you?"
    respond(markdown_message)

@app.command("/reset")
def handle_reset(ack, respond, command):
    ack()
    channel = command["channel_id"]
    user = command["user_id"]
    from_user_id = "{}:{}".format(channel,user)
    Session.clear_session(from_user_id)
    log.info('[Slack] reset session: {}'.format(from_user_id))
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

@flask_app.route("/slack/install", methods=["GET"])
def slack_install():
    state = state_store.issue()
    url = authorize_url_generator.generate(state)
    return f'<a href="{html.escape(url)}">' \
           f'<img alt=""Add to Slack"" height="40" width="139" ' \
           f'src="https://platform.slack-edge.com/img/add_to_slack.png" ' \
           f'srcset="https://platform.slack-edge.com/img/add_to_slack.png 1x, ' \
           f'https://platform.slack-edge.com/img/add_to_slack@2x.png 2x" /></a>'

@flask_app.route("/slack/oauth_redirect", methods=["GET"])
def slack_oauth_redirect():
    if "code" in request.args:
        code=request.args["code"]
        if state_store.consume(request.args["state"]):
            log.info(f"== client:{client_id} code:{code}")
            client = WebClient()
            oauth_response = client.oauth_v2_access(
                client_id=client_id,
                client_secret=client_secret,
                # redirect_uri=redirect_uri,
                code=code
            )
            # log.info(oauth_response)
            installed_enterprise = oauth_response.get("enterprise") or {}
            is_enterprise_install = oauth_response.get("is_enterprise_install")
            installed_team = oauth_response.get("team") or {}
            installer = oauth_response.get("authed_user") or {}
            incoming_webhook = oauth_response.get("incoming_webhook") or {}
            bot_token = oauth_response.get("access_token")
            bot_id = None
            enterprise_url = None
            if bot_token is not None:
                auth_test = client.auth_test(token=bot_token)
                bot_id = auth_test["bot_id"]
                if is_enterprise_install is True:
                    enterprise_url = auth_test.get("url")
            installation = Installation(
                app_id=oauth_response.get("app_id"),
                enterprise_id=installed_enterprise.get("id"),
                enterprise_name=installed_enterprise.get("name"),
                enterprise_url=enterprise_url,
                team_id=installed_team.get("id"),
                team_name=installed_team.get("name"),
                bot_token=bot_token,
                bot_id=bot_id,
                bot_user_id=oauth_response.get("bot_user_id"),
                bot_scopes=oauth_response.get("scope"),
                user_id=installer.get("id"),
                user_token=installer.get("access_token"),
                user_scopes=installer.get("scope"),
                incoming_webhook_url=incoming_webhook.get("url"),
                incoming_webhook_channel=incoming_webhook.get("channel"),
                incoming_webhook_channel_id=incoming_webhook.get("channel_id"),
                incoming_webhook_configuration_url=incoming_webhook.get("configuration_url"),
                is_enterprise_install=is_enterprise_install,
                token_type=oauth_response.get("token_type"),
            )
            installation_store.save(installation)
            return "Thanks for installing this app!"
        else:
            return make_response(f"Try the installation again (the state value is already expired)", 400)

    error = request.args["error"] if "error" in request.args else ""
    return make_response(f"Something is wrong with the installation (error: {html.escape(error)})", 400)

@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    return handler.handle(request)

@flask_app.route("/slack/commands", methods=["POST"])
def slack_commands():
    return handler.handle(request)

@flask_app.route("/slack/interactions", methods=["POST"])
def slack_interactions():
    return handler.handle(request)

@flask_app.route("/slack/postmsg", methods=["POST"])
def slack_postmsg():
    data=request.get_json()
    team_id=data.get('team')
    channel_id=data.get('channel')
    message=data.get('message')
    if team_id is None or channel_id is None or message is None:
        return make_response(f"Invalid Request", 400)
    bot_info_path=f"/opt/boltstore/installations/none-{team_id}/bot-latest"
    if not os.path.exists(bot_info_path):
        return make_response(f"Team:{team_id} Not Found", 404)
    with open(bot_info_path, "r") as bot_info_json:
        bot_info=json.load(bot_info_json)
        bot_token=bot_info["bot_token"]
    log.info(f"== client:postmsg {team_id} {channel_id} {bot_token}")
    try:
        response = app.client.chat_postMessage(
            token=bot_token,
            channel=channel_id,
            text=message
        )
    except SlackApiError as e:
        log.info(f"Failed to post a message {e.response}")
        return make_response(f"{channel_id} Message Failed: {e.response}", 400)
    return make_response(f"{channel_id} Message Posted", 200)

class SlackChannel(Channel):
    def startup(self):
        flask_app.run(host='0.0.0.0', port=channel_conf(const.SLACK).get('port'))

    def handle(self, event):
        context = dict()
        channel = event["channel"]
        channel_type = event.get("channel_type","channel")
        if channel_type=="channel" and channel[0]=='D':
            channel_type="im"
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
