# encoding:utf-8

from model.model import Model
from config import model_conf, common_conf_val
from common import const
from common import log
from common.redis import RedisSingleton
import openai
import time
import re

user_session = dict()

# OpenAI对话模型API (可用)
class ChatGPTModel(Model):
    def __init__(self):
        openai.api_key = model_conf(const.OPEN_AI).get('api_key')
        api_base = model_conf(const.OPEN_AI).get('api_base')
        if api_base:
            openai.api_base = api_base
        proxy = model_conf(const.OPEN_AI).get('proxy')
        if proxy:
            openai.proxy = proxy
        log.info("[CHATGPT] api_base={} proxy={}".format(
            api_base, proxy))
    def reply(self, query, context=None):
        # acquire reply content
        if not context or not context.get('type') or context.get('type') == 'TEXT':
            log.info("[CHATGPT] query={}".format(query))
            from_user_id = context['from_user_id']
            from_org_id = context['from_org_id']
            clear_memory_commands = common_conf_val('clear_memory_commands', ['#清除记忆'])
            if query in clear_memory_commands:
                Session.clear_session(from_user_id)
                return 'Session is reset.'

            new_query, refurls = Session.build_session_query(query, from_user_id, from_org_id)
            if new_query is None:
                return 'Sorry, I have no ideas about what you said.'
            log.debug("[CHATGPT] session query={}".format(new_query))

            # if context.get('stream'):
            #     # reply in stream
            #     return self.reply_text_stream(query, new_query, from_user_id)

            reply_content = self.reply_text(new_query, from_user_id, 0)
            #log.debug("[CHATGPT] new_query={}, user={}, reply_cont={}".format(new_query, from_user_id, reply_content))
            if len(refurls) > 0:
                for i, url in enumerate(refurls):
                    reply_content+=f"\n[{i}] {url}"
            return reply_content

        elif context.get('type', None) == 'IMAGE_CREATE':
            return self.create_img(query, 0)

    def reply_text(self, query, user_id, retry_count=0):
        try:
            response = openai.ChatCompletion.create(
                model= model_conf(const.OPEN_AI).get("model") or "gpt-3.5-turbo",  # 对话模型的名称
                messages=query,
                temperature=model_conf(const.OPEN_AI).get("temperature", 0.75),  # 熵值，在[0,1]之间，越大表示选取的候选词越随机，回复越具有不确定性，建议和top_p参数二选一使用，创意性任务越大越好，精确性任务越小越好
                #max_tokens=4096,  # 回复最大的字符数，为输入和输出的总数
                #top_p=model_conf(const.OPEN_AI).get("top_p", 0.7),,  #候选词列表。0.7 意味着只考虑前70%候选词的标记，建议和temperature参数二选一使用
                frequency_penalty=model_conf(const.OPEN_AI).get("frequency_penalty", 0.0),  # [-2,2]之间，该值越大则越降低模型一行中的重复用词，更倾向于产生不同的内容
                presence_penalty=model_conf(const.OPEN_AI).get("presence_penalty", 1.0)  # [-2,2]之间，该值越大则越不受输入限制，将鼓励模型生成输入中不存在的新词，更倾向于产生不同的内容
                )
            reply_content = response.choices[0]['message']['content']
            used_token = response['usage']['total_tokens']
            log.debug(response)
            log.info("[CHATGPT] usage={}", response['usage'])
            log.info("[CHATGPT] reply={}", reply_content)
            if reply_content:
                # save conversation
                Session.save_session(query, reply_content, user_id, used_token)
            return response.choices[0]['message']['content']
        except openai.error.RateLimitError as e:
            # rate limit exception
            log.warn(e)
            if retry_count < 1:
                time.sleep(5)
                log.warn("[CHATGPT] RateLimit exceed, 第{}次重试".format(retry_count+1))
                return self.reply_text(query, user_id, retry_count+1)
            else:
                return "提问太快啦，请休息一下再问我吧"
        except openai.error.APIConnectionError as e:
            log.warn(e)
            log.warn("[CHATGPT] APIConnection failed")
            return "我连接不到网络，请稍后重试"
        except openai.error.Timeout as e:
            log.warn(e)
            log.warn("[CHATGPT] Timeout")
            return "我没有收到消息，请稍后重试"
        except Exception as e:
            # unknown exception
            log.exception(e)
            Session.clear_session(user_id)
            return "请再问我一次吧"


    async def reply_text_stream(self, query, context, retry_count=0):
        try:
            from_user_id = context['from_user_id']
            from_org_id = context['from_org_id']
            new_query, refurls = Session.build_session_query(query, from_user_id, from_org_id)
            if new_query is None:
                yield True,'Sorry, I have no ideas about what you said.'
            res = openai.ChatCompletion.create(
                model= model_conf(const.OPEN_AI).get("model") or "gpt-3.5-turbo",  # 对话模型的名称
                messages=new_query,
                temperature=model_conf(const.OPEN_AI).get("temperature", 0.75),  # 熵值，在[0,1]之间，越大表示选取的候选词越随机，回复越具有不确定性，建议和top_p参数二选一使用，创意性任务越大越好，精确性任务越小越好
                #max_tokens=4096,  # 回复最大的字符数，为输入和输出的总数
                #top_p=model_conf(const.OPEN_AI).get("top_p", 0.7),,  #候选词列表。0.7 意味着只考虑前70%候选词的标记，建议和temperature参数二选一使用
                frequency_penalty=model_conf(const.OPEN_AI).get("frequency_penalty", 0.0),  # [-2,2]之间，该值越大则越降低模型一行中的重复用词，更倾向于产生不同的内容
                presence_penalty=model_conf(const.OPEN_AI).get("presence_penalty", 1.0),  # [-2,2]之间，该值越大则越不受输入限制，将鼓励模型生成输入中不存在的新词，更倾向于产生不同的内容
                stream=True
            )
            full_response = ""
            for chunk in res:
                log.debug(chunk)
                if (chunk["choices"][0]["finish_reason"]=="stop"):
                    break
                chunk_message = chunk['choices'][0]['delta'].get("content")
                if(chunk_message):
                    full_response+=chunk_message
                yield False,full_response
            Session.save_session(query, full_response, from_user_id)
            log.info("[chatgpt]: reply={}", full_response)
            if len(refurls) > 0:
                for i, url in enumerate(refurls):
                    full_response+=f"\n[{i}] {url}"
            yield True,full_response

        except openai.error.RateLimitError as e:
            # rate limit exception
            log.warn(e)
            if retry_count < 1:
                time.sleep(5)
                log.warn("[CHATGPT] RateLimit exceed, 第{}次重试".format(retry_count+1))
                yield True, self.reply_text_stream(query, from_user_id, retry_count+1)
            else:
                yield True, "提问太快啦，请休息一下再问我吧"
        except openai.error.APIConnectionError as e:
            log.warn(e)
            log.warn("[CHATGPT] APIConnection failed")
            yield True, "我连接不到网络，请稍后重试"
        except openai.error.Timeout as e:
            log.warn(e)
            log.warn("[CHATGPT] Timeout")
            yield True, "我没有收到消息，请稍后重试"
        except Exception as e:
            # unknown exception
            log.exception(e)
            Session.clear_session(from_user_id)
            yield True, "请再问我一次吧"

    def create_img(self, query, retry_count=0):
        try:
            log.info("[OPEN_AI] image_query={}".format(query))
            response = openai.Image.create(
                prompt=query,    #图片描述
                n=1,             #每次生成图片的数量
                size="256x256"   #图片大小,可选有 256x256, 512x512, 1024x1024
            )
            image_url = response['data'][0]['url']
            log.info("[OPEN_AI] image_url={}".format(image_url))
            return [image_url]
        except openai.error.RateLimitError as e:
            log.warn(e)
            if retry_count < 1:
                time.sleep(5)
                log.warn("[OPEN_AI] ImgCreate RateLimit exceed, 第{}次重试".format(retry_count+1))
                return self.reply_text(query, retry_count+1)
            else:
                return "提问太快啦，请休息一下再问我吧"
        except Exception as e:
            log.exception(e)
            return None


class Session(object):
    @staticmethod
    def build_session_query(query, user_id, org_id):
        '''
        build query with conversation history
        e.g.  [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Who won the world series in 2020?"},
            {"role": "assistant", "content": "The Los Angeles Dodgers won the World Series in 2020."},
            {"role": "user", "content": "Where was it played?"}
        ]
        :param query: query content
        :param user_id: from user id
        :return: query content with conversaction
        '''
        pattern = r'org:(\d+)'
        match = re.search(pattern, org_id)
        orgno = 0
        if match:
            orgno = int(match.group(1))

        refurls = []
        session = user_session.get(user_id, [])
        myquery = openai.Embedding.create(input=query, model="text-embedding-ada-002")["data"][0]['embedding']
        myredis = RedisSingleton()
        docs = myredis.ft_search(embedded_query=myquery, hybrid_fields=myredis.create_hybrid_field(str(orgno), "category", "kb"))

        threshold = model_conf(const.OPEN_AI).get("similarity_threshold", 0.3)
        if len(docs) > 0 and float(docs[0].vector_score) > float(threshold):
            log.info(f"[CHATGPT] score:{docs[0].vector_score} > threshold:{threshold}")
            return None, []
        if len(session) > 0 and session[0]['role'] == 'system':
            session.pop(0)
        # system_prompt = model_conf(const.OPEN_AI).get("character_desc", "")
        system_prompt = myredis.redis.hget('sfbot:'+org_id, 'character_desc').decode()
        if len(docs) > 0:
            system_prompt += '\nPlease respond to customer inquiries based on the following context, which is separated by 3 backticks.'
            system_prompt += '\nReply \"Sorry, I have no ideas.\", If you don\'t know the answer or you are not sure, don\'t try to make it up.'
            system_prompt += '\nReply \"Sorry, can you describe more clearly?\", if you are unclear about customer inquiry.'
            system_prompt += '\nContext:\n```'
            for i, doc in enumerate(docs):
                log.info(f"{i}) {doc.id} {doc.category} {doc.vector_score}")
                system_prompt += '\n' + myredis.redis.hget(doc.id, 'text').decode()
                if float(doc.vector_score) < 0.18:
                    docurl = myredis.hget(doc.id, 'source')
                    if docurl is not None:
                        refurls.append(docurl)
            system_prompt += '\n```\n'
            refurls = list(set(refurls))
        log.info("[CHATGPT] prompt={}".format(system_prompt))
        system_item = {'role': 'system', 'content': system_prompt}
        session.insert(0, system_item)
        user_session[user_id] = session
        user_item = {'role': 'user', 'content': query}
        session.append(user_item)
        return session, refurls

    @staticmethod
    def save_session(query, answer, user_id, used_tokens=0):
        max_tokens = model_conf(const.OPEN_AI).get('conversation_max_tokens')
        max_history_num = model_conf(const.OPEN_AI).get('max_history_num', None)
        if not max_tokens or max_tokens > 4000:
            # default value
            max_tokens = 1000
        session = user_session.get(user_id)
        if session:
            # append conversation
            gpt_item = {'role': 'assistant', 'content': answer}
            session.append(gpt_item)

        if used_tokens > max_tokens and len(session) >= 3:
            # pop first conversation (TODO: more accurate calculation)
            session.pop(1)
            session.pop(1)

        if max_history_num is not None:
            while len(session) > max_history_num * 2 + 1:
                session.pop(1)
                session.pop(1)

    @staticmethod
    def clear_session(user_id):
        user_session[user_id] = []

