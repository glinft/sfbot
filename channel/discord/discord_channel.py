# encoding:utf-8

"""
discord channel
Python discord - https://github.com/Rapptz/discord.py.git
"""
from channel.channel import Channel
from common import log
from common.redis import RedisSingleton
from config import conf, common_conf_val, channel_conf
import json
import ssl
import discord
from discord.ext import commands
from discord import app_commands

class DiscordChannel(Channel):

    def __init__(self):
        config = conf()

        self.token = channel_conf('discord').get('app_token')
        self.discord_channel_name = channel_conf('discord').get('channel_name')
        self.discord_channel_name = None
        self.discord_channel_session = channel_conf('discord').get('channel_session', 'author')
        self.voice_enabled = channel_conf('discord').get('voice_enabled', False)
        self.cmd_clear_session = common_conf_val('clear_memory_commands', ['#清除记忆'])[0]
        self.sessions = []
        self.intents = discord.Intents.default()
        self.intents.message_content = True
        self.intents.guilds = True
        self.intents.members = True
        self.intents.messages = True
        self.intents.voice_states = True

        context = ssl.create_default_context()
        context.load_verify_locations(common_conf_val('certificate_file'))
        self.bot = commands.Bot(command_prefix='!', intents=self.intents, ssl=context)
        self.bot.add_listener(self.on_ready)

        log.debug('cmd_clear_session %s', self.cmd_clear_session)

    def startup(self):
        self.bot.add_listener(self.on_message)
        self.bot.add_listener(self.on_guild_channel_delete)
        self.bot.add_listener(self.on_guild_channel_create)
        self.bot.add_listener(self.on_private_channel_delete)
        self.bot.add_listener(self.on_private_channel_create)
        self.bot.add_listener(self.on_channel_delete)
        self.bot.add_listener(self.on_channel_create)
        self.bot.add_listener(self.on_thread_delete)
        self.bot.add_listener(self.on_thread_create)
        self.bot.run(self.token)

    async def on_ready(self):
        log.info('Bot is online user:{}:{}'.format(self.bot.user,self.bot.user.id))
        if self.voice_enabled == False:
            log.debug('disable music')
            await self.bot.remove_cog("Music")

    async def join(self, ctx):
        log.debug('join %s', repr(ctx))
        channel = ctx.author.voice.channel
        await channel.connect()

    async def _do_on_channel_delete(self, channel):
        if not self.discord_channel_name or channel.name != self.discord_channel_name:
            log.debug('skip _do_on_channel_delete %s', channel.name)
            return

        for name in self.sessions:
            try:
                response = self.send_text(name, self.cmd_clear_session)
                log.debug('_do_on_channel_delete %s %s', channel.name, response)
            except Exception as e:
                log.warn('clear session except, id:%s', name)

        self.sessions.clear()

    async def on_guild_channel_delete(self, channel):
        log.debug('on_guild_channel_delete %s', repr(channel))
        await self._do_on_channel_delete(channel)

    async def on_guild_channel_create(self, channel):
        log.debug('on_guild_channel_create %s', repr(channel))

    async def on_private_channel_delete(self, channel):
        log.debug('on_channel_delete %s', repr(channel))
        await self._do_on_channel_delete(channel)

    async def on_private_channel_create(self, channel):
        log.debug('on_channel_create %s', repr(channel))

    async def on_channel_delete(self, channel):
        log.debug('on_channel_delete %s', repr(channel))

    async def on_channel_create(self, channel):
        log.debug('on_channel_create %s', repr(channel))

    async def on_thread_delete(self, thread):
        log.debug('on_thread_delete %s %s', thread.id, thread.parent.name)
        if self.discord_channel_session != 'thread' or thread.parent.name != self.discord_channel_name:
            log.debug('skip on_thread_delete %s', thread.id)
            return

        try:
            response = self.send_text(thread.id, self.cmd_clear_session)
            if thread.id in self.sessions:
                self.sessions.remove(thread.id)
            log.debug('on_thread_delete %s %s', thread.id, response)
        except Exception as e:
            log.warn('on_thread_delete except %s', thread.id)
            raise e


    async def on_thread_create(self, thread):
        log.debug('on_thread_create %s %s', thread.id, thread.parent.name)
        if self.discord_channel_session != 'thread' or thread.parent.name != self.discord_channel_name:
            log.debug('skip on_channel_create %s', repr(thread))
            return

        self.sessions.append(thread.id)

    async def on_message(self, message):
        """
        listen for message event
        """
        await self.bot.wait_until_ready()
        if not self.check_message(message):
            return

        prompt = message.content.strip();
        log.debug('author: %s', message.author)
        log.debug('prompt: %s', prompt)
        if prompt.lower() == '/help':
            markdown_message = "Hi, I am a chatbot _powered by_ [Easiio](https://www.easiio.com/). What can I do for you?"
            await message.channel.send(markdown_message)
            return
        elif prompt.lower() == '/getid':
            author=message.author
            channel=message.channel
            guild=message.channel.guild
            if isinstance(channel, discord.channel.DMChannel):
                channelname="@"+author.name
                markdown_message = "*User ID*: {}\n*Username*: {}".format(channel.id, channelname)
            else:
                channelname="#"+channel.name
                if guild is not None:
                    channelname=guild.name+" "+channelname
                markdown_message = "*Channel ID*: {}\n*Channel Title*: {}".format(channel.id, channelname)
            await message.channel.send(markdown_message)
            return

        if not isinstance(message.channel, discord.channel.DMChannel):
            mentionuser = "<@{}>".format(self.bot.user.id)
            if mentionuser not in prompt:
                return
            prompt = prompt.replace(mentionuser, "")

        session_id = str(message.author)
        if self.discord_channel_session == 'thread' and isinstance(message.channel, discord.Thread):
            log.debug('on_message thread id %s', message.channel.id)
            session_id = str(message.channel.id)

        dot3 = await message.channel.send('...')
        response = self.send_text(session_id, prompt, message.channel)
        await dot3.delete()
        await message.channel.send(response)

    def dump_object(self, mo):
        attributes = dir(mo)
        for attr in attributes:
            try:
                value = getattr(mo, attr)
                log.info('##DC id:%s value:%s', attr, value)
            except Exception as e:
                log.warn('##DC id:%s error:%s', attr, e)

    def check_message(self, message):
        if message.author == self.bot.user:
            return False

        prompt = message.content.strip();
        if not prompt:
            log.debug('no prompt author: %s', message.author)
            return False

        #self.dump_object(message)
        #self.dump_object(message.author)
        #self.dump_object(message.channel)
        author=message.author
        channel=message.channel
        guild=message.channel.guild
        if isinstance(channel, discord.channel.DMChannel):
            chattype="private"
            channelname="@"+author.name
        else:
            chattype="channel"
            channelname="#"+channel.name
            if guild is not None:
                channelname=guild.name+" "+channelname
        log.info('discord/config %s %s', self.discord_channel_session, self.discord_channel_name)
        log.info('discord/message (%s)%s %s', type(message), message.id, chattype)
        log.info('discord/channel (%s)%s %s %s', type(channel), channel.id, channelname, channel.type)

        if isinstance(message.channel, discord.Thread):
            log.info('check_message/thread %s %s %s', message.channel.id, message.channel.parent.name, type(message.channel.parent))

        if self.discord_channel_name:
            if isinstance(message.channel, discord.Thread) and message.channel.parent.name == self.discord_channel_name:
                return True
            if not isinstance(message.channel, discord.Thread) and self.discord_channel_session != 'thread' and message.channel.name == self.discord_channel_name:
                return True

            log.debug("The accessed channel does not meet the discord channel configuration conditions.")
            return False
        else:
            return True

    def send_text(self, id, query, channel=None):
        context = dict()
        context['type'] = 'TEXT'
        context['from_user_id'] = id
        context['from_org_id'] = "org:4:bot:9"
        if channel is not None:
            chattype="channel"
            if isinstance(channel, discord.channel.DMChannel):
                chattype="private"
            routekey="discord:"+chattype+":"+str(channel.id)
            myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
            orgbot = myredis.redis.hget('sfbot:route', routekey)
            if orgbot is not None:
                context['from_org_id'] = orgbot.decode()
        context['res'] = "0"
        context['userflag'] = "external"
        context['character_desc'] = "undef"
        context['temperature'] = "undef"
        context['content'] = query
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
        log.info('[Discord] reply content: {}'.format(reply_text))
        return reply_text
