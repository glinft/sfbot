#!/usr/bin/env python
# -*- coding=utf-8 -*-
"""
@time: 2023/4/10 22:24
@Project ：bot-on-anything
@file: wechat_com_channel.py

"""
import time
import json

from channel.channel import Channel
from concurrent.futures import ThreadPoolExecutor
from common.log import logger
from common.redis import RedisSingleton
from config import conf, common_conf_val

from wechatpy.enterprise.crypto import WeChatCrypto
from wechatpy.enterprise import WeChatClient
from wechatpy.exceptions import InvalidSignatureException
from wechatpy.enterprise.exceptions import InvalidCorpIdException
from wechatpy.enterprise import parse_message
from flask import Flask, request, abort

thread_pool = ThreadPoolExecutor(max_workers=8)
app = Flask(__name__)


@app.route('/wxcom', methods=['GET', 'POST'])
def handler_msg():
    return WechatEnterpriseChannel().handle()


_conf = conf().get("channel").get("wechat_com")


class WechatEnterpriseChannel(Channel):
    def __init__(self):
        self.CorpId = _conf.get('wechat_corp_id')
        self.Secret = _conf.get('secret')
        self.AppId = _conf.get('appid')
        self.TOKEN = _conf.get('wechat_token')
        self.EncodingAESKey = _conf.get('wechat_encoding_aes_key')
        self.crypto = WeChatCrypto(self.TOKEN, self.EncodingAESKey, self.CorpId)
        self.client = WeChatClient(self.CorpId, self.Secret, self.AppId)

    def startup(self):
        # start message listener
        app.run(host='0.0.0.0', port=_conf.get('port'))

    def send(self, msg, receiver):
        # 切片长度
        n = 1024
        if len(msg) < n:
          logger.info('[WXCOM] sendMsg={}, receiver={}'.format(msg, receiver))
          self.client.message.send_markdown(self.AppId, receiver, msg)
          return
        # 分割后的子字符串列表
        chunks = [msg[i:i+n] for i in range(0, len(msg), n)]
        # 总消息数
        total = len(chunks)
        # 循环发送每个子字符串
        for i, chunk in enumerate(chunks):
            logger.info('[WXCOM] sendMsg={}, receiver={}, page_number={}, page_total={}'.format(msg, chunk, i+1, total))
            self.client.message.send_markdown(self.AppId, receiver, chunk)
            time.sleep(1) # 用延迟的方式使微信插件的输出顺序正常

    def _do_send(self, query, reply_user_id, agent):
        try:
            if not query:
                return
            context = dict()
            context['from_user_id'] = reply_user_id
            context['res'] = "0"
            context['userflag'] = "external"
            context['character_desc'] = "undef"
            context['temperature'] = "undef"
            context['from_org_id'] = "org:3:bot:34"
            routekey="wecom:private:"+str(agent)
            myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
            orgbot = myredis.redis.hget('sfbot:route', routekey)
            if orgbot is not None:
                context['from_org_id'] = orgbot.decode()
            reply_text = super().build_reply_content(query, context)
            if reply_text:
                splits=reply_text.split("```sf-json")
                if len(splits)==2:
                    extra=json.loads(splits[1][1:-4])
                    reply_text=splits[0]
                    pages=extra.get('pages',[])
                    if len(pages)>0:
                        reply_text+="\n"
                        for page in pages:
                            reply_text+="\n[{}]({})".format(page['title'],page['url'])
                logger.info('[WXCOM] reply content: {}'.format(reply_text))
                self.send(reply_text, reply_user_id)
        except Exception as e:
            logger.exception(e)

    def handle(self):
        query_params = request.args
        signature = query_params.get('msg_signature', '')
        timestamp = query_params.get('timestamp', '')
        nonce = query_params.get('nonce', '')
        if request.method == 'GET':
            # 处理验证请求
            echostr = query_params.get('echostr', '')
            try:
                echostr = self.crypto.check_signature(signature, timestamp, nonce, echostr)
            except InvalidSignatureException:
                abort(403)
            print(echostr)
            return echostr
        elif request.method == 'POST':
            try:
                message = self.crypto.decrypt_message(
                    request.data,
                    signature,
                    timestamp,
                    nonce
                )
            except (InvalidSignatureException, InvalidCorpIdException):
                abort(403)
            msg = parse_message(message)
            logger.info('[WXCOM] recvMsg={}'.format(msg))
            logger.info('[WXCOM] Msg/{}: {} {} {}'.format(msg.id,msg.agent,msg.source,msg.target))
            if msg.type == 'text':
                thread_pool.submit(self._do_send, msg.content, msg.source, msg.agent)
            else:
                reply = 'Can not handle this for now'
                # 未能处理的消息或菜单事件暂不做响应优化用户体验
                # self.client.message.send_text(self.AppId, msg.source, reply)
            return 'success'
"""
[WXCOM] recvMsg=TextMessage({
id 'MsgId': '7271137854683113513',
target 'ToUserName': 'ww4b4ebce7f1212c1a',
source 'FromUserName': 'glenn.lin',
content 'Content': 'what is the key application of titan S1?',
create_/time 'CreateTime': '1692943706',
'MsgType': 'text',
'AgentID': '1000003'})
"""
