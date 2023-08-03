from concurrent.futures import ThreadPoolExecutor
import io
import json
import requests
import telebot
from common import const
from common.log import logger
from common.redis import RedisSingleton
from channel.channel import Channel
from config import channel_conf_val, channel_conf, common_conf_val
bot = telebot.TeleBot(token=channel_conf(const.TELEGRAM).get('bot_token'))
bme = bot.get_me()
thread_pool = ThreadPoolExecutor(max_workers=8)

@bot.message_handler(commands=['help'])
def handle_help(msg):
    logger.info('##TG bot:(%s:%s) msg:(%s)', bme.id, bme.username, msg.json)
    markdown_message = "Hi, I am a chatbot _powered by_ [Easiio](https://www.easiio.com/). What can I do for you?"
    # bot.send_message(msg.chat.id, text=markdown_message, parse_mode="HTML")
    bot.send_message(msg.chat.id, text=markdown_message, parse_mode='Markdown')

@bot.message_handler(commands=['getid'])
def handle_getid(msg):
    logger.info('##TG bot:(%s:%s) msg:(%s)', bme.id, bme.username, msg.json)
    logger.info('##TG msg:(%s %s %s %s)', msg.date, msg.chat.id, msg.chat.type, msg.entities[0].type)
    if msg.chat.type == "private":
        logger.info('##TG msg:(%s/%s)(%s %s)', msg.chat.id, msg.chat.username, msg.chat.first_name, msg.chat.last_name)
        bot.send_message(msg.chat.id, "*User ID*: {}\n*Username*: {}".format(msg.chat.id, msg.chat.username), parse_mode = "Markdown")
    elif msg.chat.type == "group":
        logger.info('##TG msg:(%s/%s)(%s %s)(%s)', msg.from_user.id, msg.from_user.username, msg.from_user.first_name, msg.from_user.last_name, msg.chat.title)
        bot.send_message(msg.chat.id, "*Group ID*: {}\n*Group Title*: {}".format(msg.chat.id, msg.chat.title), parse_mode = "Markdown")
    elif msg.chat.type == "supergroup":
        logger.info('##TG msg:(%s/%s)(%s %s)(%s)', msg.from_user.id, msg.from_user.username, msg.from_user.first_name, msg.from_user.last_name, msg.chat.title)
        bot.send_message(msg.chat.id, "*Group ID*: {}\n*Group Title*: {}".format(msg.chat.id, msg.chat.title), parse_mode = "Markdown")
    else:
        pass

@bot.channel_post_handler(commands=['getid'])
def handle_getid_channel(msg):
    logger.info('##TG bot:(%s:%s) msg:(%s)', bme.id, bme.username, msg.json)
    bot.send_message(msg.chat.id, "*Channel ID*: {}\n*Channel Title*: {}".format(msg.chat.id, msg.chat.title), parse_mode = "Markdown")

@bot.message_handler(content_types=['text'])
def handle_message(msg):
    logger.info('##TG msg %s', msg.json)
    mentionuser = '@'+bme.username
    if msg.chat.type == "private":
        pass
    elif msg.chat.type == "group":
        if mentionuser not in msg.text:
            return
    elif msg.chat.type == "supergroup":
        if mentionuser not in msg.text:
            return
    else:
        return
    msg.text = msg.text.replace(mentionuser, "")
    TelegramChannel().handle(msg)

@bot.channel_post_handler(content_types=['text'])
def handle_channel_post(msg):
    logger.info('##TG msg %s', msg.json)
    mentionuser = '@'+bme.username
    if mentionuser not in msg.text:
        return
    msg.text = msg.text.replace(mentionuser, "")
    TelegramChannel().handle(msg)

class TelegramChannel(Channel):
    def __init__(self):
        pass

    def startup(self):
        logger.info("开始启动[telegram]机器人")
        bot.infinity_polling()

    def handle(self, msg):
        logger.debug("[Telegram] receive msg: " + msg.text)
        img_match_prefix = self.check_prefix(msg, channel_conf_val(const.TELEGRAM, 'image_create_prefix'))
        # 如果是图片请求
        if img_match_prefix:
            thread_pool.submit(self._do_send_img, msg, str(msg.chat.id))
        else:
            thread_pool.submit(self._dosend,msg.text,msg)

    def _dosend(self,query,msg):
        context= dict()
        context['from_user_id'] = str(msg.chat.id)
        context['from_org_id'] = "org:4:bot:9"
        chattype=msg.chat.type
        if msg.chat.type == "supergroup":
            chattype="group"
        routekey="telegram:"+chattype+":"+str(msg.chat.id)
        myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
        orgbot = myredis.redis.hget('sfbot:route', routekey)
        if orgbot is not None:
            context['from_org_id'] = orgbot.decode()
        logger.info('[Telegram] route: {}'.format(context['from_org_id']))
        context['res'] = "0"
        context['userflag'] = "external"
        context['character_desc'] = "undef"
        context['temperature'] = "undef"
        reply_text = super().build_reply_content(query, context)
        splits=reply_text.split("```sf-json")
        if len(splits)==2:
            extra=json.loads(splits[1][1:-4])
            reply_text=splits[0]
            pages=extra.get('pages',[])
            if len(pages)>0:
                reply_text+="\n"
                for page in pages:
                    reply_text+="\n[{}]({})".format(page['title'],page['url'])
        logger.info('[Telegram] reply content: {}'.format(reply_text))
        bot.reply_to(msg, text=reply_text, parse_mode='Markdown')

    def _do_send_img(self, msg, reply_user_id):
        try:
            if not msg:
                return
            context = dict()
            context['type'] = 'IMAGE_CREATE'
            img_urls = super().build_reply_content(msg.text, context)
            if not img_urls:
                return
            if not isinstance(img_urls, list):
                bot.reply_to(msg,img_urls)
                return
            for url in img_urls:
            # 图片下载
                pic_res = requests.get(url, stream=True)
                image_storage = io.BytesIO()
                for block in pic_res.iter_content(1024):
                    image_storage.write(block)
                image_storage.seek(0)

                # 图片发送
                logger.info('[Telegrame] sendImage, receiver={}'.format(reply_user_id))
                bot.send_photo(msg.chat.id,image_storage)
        except Exception as e:
            logger.exception(e)

    def check_prefix(self, msg, prefix_list):
        if not prefix_list:
            return None
        for prefix in prefix_list:
            if msg.text.startswith(prefix):
                return prefix
        return None
