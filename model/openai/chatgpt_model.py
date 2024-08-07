# encoding:utf-8

from model.model import Model
from config import model_conf, common_conf_val
from common import const
from common import log
from common.redis import RedisSingleton
from common.word_filter import WordFilter
from openai import OpenAI
from openai import AzureOpenAI
import os
import time
import json
import re
import requests
import base64
import random
import string
import hashlib
import openai
import tiktoken
import oss2
import uuid
from io import BytesIO
from PIL import Image
from datetime import datetime
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_community.chat_models import QianfanChatEndpoint
from langchain_community.vectorstores import FAISS
from langchain_community.callbacks import get_openai_callback
from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI
from urllib.parse import urlparse, urlunparse
from duckduckgo_search import DDGS

oai_key = model_conf(const.OPEN_AI).get('api_key')
oai_embeddings_model = "text-embedding-ada-002"
oai_embeddings = OpenAIEmbeddings(openai_api_key=oai_key, model=oai_embeddings_model)
client = OpenAI(
    base_url=model_conf(const.OPEN_AI).get('api_base'),
    api_key=oai_key,
)
azurec = AzureOpenAI(
    azure_endpoint=model_conf(const.OPEN_AI).get('azure_api_base'),
    api_key=model_conf(const.OPEN_AI).get('azure_api_key'),
    api_version="2023-05-15",
)

qfn_ak = common_conf_val("qianfan_ak", "xxx")
qfn_sk = common_conf_val("qianfan_sk", "xxx")
gmn_key = common_conf_val("google_api_key", "xxx")
llmgpt = ChatOpenAI(temperature=0, model_name="gpt-4o-mini", openai_api_key=oai_key)

user_session = dict()
context_tokens = 8192
md5sum_pattern = r'^[0-9a-f]{32}$'
faiss_store_root= "/opt/faiss/"

def is_valid_uuid(uuidstr):
    pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    return bool(re.match(pattern, uuidstr, re.IGNORECASE))

def generate_random_string(length=8):
    characters = string.ascii_lowercase + string.digits
    return ''.join(random.choices(characters, k=length))

def calculate_md5(text):
    md5_hash = hashlib.md5()
    md5_hash.update(text.encode('utf-8'))
    return md5_hash.hexdigest()

def get_org_bot(input_string):
    parts = input_string.split(':')
    org_part = ":".join(parts[:2])
    bot_part = ":".join(parts[2:])
    return org_part, bot_part

def get_org_id(string):
    pattern = r'org:(\d+)'
    match = re.search(pattern, string)
    orgid = 0
    if match:
        orgid = int(match.group(1))
    return orgid

def get_bot_id(string):
    pattern = r'bot:(\d+)'
    match = re.search(pattern, string)
    botid = 0
    if match:
        botid = int(match.group(1))
    return botid

def get_unique_by_key(data, key):
    seen = set()
    unique_list = [d for d in data if d.get(key) not in seen and not seen.add(d.get(key))]
    return unique_list

def num_tokens_from_string(string):
    encoding = tiktoken.get_encoding("cl100k_base")
    num_tokens = len(encoding.encode(string))
    return num_tokens

def num_tokens_from_messages(messages):
    encoding = tiktoken.get_encoding("cl100k_base")
    tokens_per_message = 4
    tokens_per_name = -1
    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            num_tokens += len(encoding.encode(value))
            if key == "name":
                num_tokens += tokens_per_name
    num_tokens += 3
    return num_tokens

def remove_url_query(url):
    parsed_url = urlparse(url)
    clean_url = urlunparse((parsed_url.scheme, parsed_url.netloc, parsed_url.path, '', '', ''))
    return clean_url

def is_image_url(url):
    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp']
    lower_url = url.lower()
    return any(lower_url.endswith(ext) for ext in image_extensions)

def is_video_url(url):
    video_extensions = ['.mp4', '.webm', '.ogv']
    lower_url = url.lower()
    return any(lower_url.endswith(ext) for ext in video_extensions)

def increase_hit_count(fid, category, url=''):
    gqlurl = 'http://127.0.0.1:5000/graphql'
    gqlfunc = 'increaseHitCount'
    headers = { "Content-Type": "application/json", }
    query = f"mutation {gqlfunc} {{ {gqlfunc}( id:{fid}, category:\"{category}\", url:\"{url}\" ) }}"
    gqldata = { "query": query, "variables": {}, }
    try:
        gqlresp = requests.post(gqlurl, json=gqldata, headers=headers)
        log.info(f"GQL/{gqlfunc}: #{fid} {gqlresp.status_code} {query}")
        log.debug(f"GQL/{gqlfunc}: #{fid} {gqlresp.json()}")
    except Exception as e:
        log.exception(e)

def send_query_notification(rid, str1, str2):
    gqlurl = 'http://127.0.0.1:5000/graphql'
    gqlfunc = 'notiSfbotNotification'
    headers = { "Content-Type": "application/json", }
    chatstr = f"{str1}\n\n{str2}"
    content = base64.b64encode(chatstr.encode('utf-8')).decode('utf-8')
    query = f"mutation {gqlfunc} {{ {gqlfunc}( id:{rid}, content:\"{content}\" ) }}"
    gqldata = { "query": query, "variables": {}, }
    try:
        gqlresp = requests.post(gqlurl, json=gqldata, headers=headers)
        log.info(f"GQL/{gqlfunc}: #{rid} {gqlresp.status_code} {query}")
        log.debug(f"GQL/{gqlfunc}: #{rid} {gqlresp.json()}")
    except Exception as e:
        log.exception(e)

def run_word_filter(text, org_id):
    wftool = WordFilter()
    wfdict,_ = wftool.load_words(0)
    if int(org_id)>0:
        wfdict_org,_ = wftool.load_words(org_id)
        wfdict.update(wfdict_org)
    filted_text = wftool.replace_sensitive_words(text, wfdict)
    return filted_text

def get_plaid_balance_data(user_id):
    url = f"https://api.sflow.io/plaid/api/balance_data/{user_id}"
    sfresp = requests.get(url)
    if sfresp.status_code==200:
        blresp = json.loads(sfresp.text)
        bldata = blresp["Balance"]
        if len(bldata["accounts"])==0:
            return "Accounts: NO DATA\n\n"
        result = ""
        for idx,acc in enumerate(bldata["accounts"], start=1):
            result += f"Account {idx}:\n"
            result += f"- Name: {acc['name']}\n"
            result += f"- Official Name: {acc['official_name']}\n"
            result += f"- Type: {acc['type']}\n"
            result += f"- Subtype: {acc['subtype']}\n"
            result += f"- Balances: "
            result += f"Available={acc['balances']['available']}, "
            result += f"Current={acc['balances']['current']}, "
            result += f"CurrencyCode={acc['balances']['iso_currency_code']}, "
            result += f"Limit={acc['balances']['limit']}\n\n"
        return result
    return "Accounts: NO DATA\n\n"

def get_plaid_transactions_data(user_id, start_date, end_date):
    url = f"https://api.sflow.io/plaid/api/transactions_data/{user_id}"
    payload = {'start_date': start_date, 'end_date': end_date}
    sfresp = requests.post(url, json=payload)
    if sfresp.status_code==200:
        txresp = json.loads(sfresp.text)
        txdata = txresp["Transactions"]
        if len(txdata["transactions"])==0:
            # return "Transactions: NO DATA\n\n"
            with open('/home/ezoweb/devel/sfplaid/plaidtx.json', 'r') as txfile:
                txresp = json.load(txfile)
                txdata = txresp["Transactions"]
        result = ""
        for idx,txn in enumerate(txdata["transactions"], start=1):
            result += f"Transaction {idx}:\n"
            result += f"- Datetime: {txn['datetime']}\n"
            result += f"- Amount: {txn['amount']}\n"
            result += f"- CurrencyCode: {txn['iso_currency_code']}\n"
            result += f"- Category: {', '.join(txn['category'])}\n"
            result += f"- Name: {txn['name']}\n"
            result += f"- MerchantName: {txn['merchant_name']}\n"
            result += f"- PaymentChannel: {txn['payment_channel']}\n"
            result += f"- Type: {txn['transaction_type']}\n\n"
        return result
    return "Transactions: NO DATA\n\n"

plaid_funcs = { "get_plaid_balance_data": get_plaid_balance_data, "get_plaid_transactions_data": get_plaid_transactions_data, }
plaid_tools = [
    {
        "type": "function",
        "function": {
            "name": "get_plaid_balance_data",
            "description": "Inquire about the current account balance from Plaid.",
            "parameters": { "type": "object", "properties": {}, "required": [], },
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_plaid_transactions_data",
            "description": "Retrieve transaction data within the start date and end date from Plaid.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": { "type": "string", "description": "The start date, e.g. 2024-01-01", },
                    "end_date": { "type": "string", "description": "The end date, e.g. 2024-01-07", },
                },
                "required": ["start_date", "end_date"],
            },
        }
    },
]

def get_latest_news(query):
    query=query.strip()
    if len(query) == 0:
        return None
    ddgs_result=[]
    with DDGS(timeout=5) as ddgs:
        for r in ddgs.news(query, max_results=10):
            ddgs_result.append(r)
    return json.dumps(ddgs_result)

# OpenAI对话模型API (可用)
class ChatGPTModel(Model):
    def __init__(self):
        api_base = model_conf(const.OPEN_AI).get('api_base')
        log.info("[CHATGPT] api_base={}".format(api_base))
        azure_api_base = model_conf(const.OPEN_AI).get('azure_api_base')
        log.info("[CHATGPT] azure_api_base={}".format(azure_api_base))

    def select_gpt_service(self, vendor='default'):
        if vendor == 'azure':
            log.info("[CHATGPT] gpt_vendor={}".format(vendor))
        else:
            log.info("[CHATGPT] gpt_vendor={}".format(vendor))

    def reply(self, query, context=None):
        # acquire reply content
        if not context or not context.get('type') or context.get('type') == 'TEXT':
            log.info("[CHATGPT] context={}".format(context))
            log.info("[CHATGPT] query={}".format(query))
            from_user_id = context['from_user_id']
            from_org_id = context['from_org_id']
            from_org_id, from_chatbot_id = get_org_bot(from_org_id)
            user_flag = context['userflag']
            user_uuid = context.get('userid','undef')
            user_asst = context.get('userasst','undef')
            file_ids = context.get('fileids','0')
            nres = int(context.get('res','0'))
            fwd = int(context.get('fwd','0'))
            ctx = int(context.get('ctx','0'))
            character_id = context.get('character_id')
            character_desc = context.get('character_desc')
            temperature = context['temperature']
            website = context.get('website','undef')
            email = context.get('email','undef')
            sfmodel = context.get('sfmodel','undef')
            sfuserid = context.get('sfuserid','undef')
            llm_provider = 'openai'
            llm_credential = 'default'
            if not is_valid_uuid(user_uuid):
                user_uuid = None
            if not is_valid_uuid(user_asst):
                user_asst = None
            if not (isinstance(sfmodel, str) and (sfmodel.startswith('ft:') or sfmodel.startswith('gpt-'))):
                sfmodel = None

            if isinstance(file_ids, str) and file_ids != '0' and from_chatbot_id.startswith('user:'):
                character_id = file_ids
                from_chatbot_id = 'bot:file'
                user_flag = 'internal'
                user_uuid = None
                user_asst = None
                website = None
                email = None
                nres = 0
                fwd = 0
                ctx = 0

            clear_memory_commands = common_conf_val('clear_memory_commands', ['#清除记忆'])
            if query in clear_memory_commands:
                log.info('[CHATGPT] reset session: {}'.format(from_user_id))
                Session.clear_session(from_user_id)
                return 'Session is reset.'

            query_embedding = oai_embeddings.embed_query(query)
            orgnum = str(get_org_id(from_org_id))
            botnum = str(get_bot_id(from_chatbot_id))
            myredis = RedisSingleton(password=common_conf_val('redis_password', ''))

            if user_uuid:
                plaid_msgs = [
                    {'role':'system', 'content':"You are an agent of Plaid, a digital financial service provider, and you try to handle the query about accounts or transactions."},
                    {"role":"user", "content":"Today is "+datetime.now().strftime("%Y-%m-%d")+". "+query}
                ]
                plaid_qcmp = client.chat.completions.create(model="gpt-4o-mini", messages=plaid_msgs, tools=plaid_tools, tool_choice="auto")
                plaid_qrsp = plaid_qcmp.choices[0].message
                if plaid_qrsp.tool_calls is not None:
                    plaid_msgs.append(plaid_qrsp)
                    for tc in plaid_qrsp.tool_calls:
                        if tc.type=="function":
                            function_name=tc.function.name
                            function_args = json.loads(tc.function.arguments)
                            function_args["user_id"] = user_uuid
                            function_tocall = plaid_funcs[function_name]
                            function_output = function_tocall(**function_args)
                            plaid_msgs.append({"tool_call_id":tc.id, "role":"tool", "name":function_name, "content":function_output})
                    plaid_fcmp = client.chat.completions.create(model="gpt-4o-mini", messages=plaid_msgs)
                    reply_content = plaid_fcmp.choices[0].message.content
                    log.info("[PLAID]  msgs={}", plaid_msgs)
                    log.info("[PLAID] reply={}", reply_content)
                    model_name = plaid_fcmp.model
                    used_tokens = plaid_fcmp.usage.total_tokens
                    prompt_tokens = plaid_fcmp.usage.prompt_tokens
                    completion_tokens = plaid_fcmp.usage.completion_tokens
                    logid = Session.save_session(query, reply_content, from_user_id, from_org_id, from_chatbot_id, sfuserid, model_name, used_tokens, prompt_tokens, completion_tokens)
                    reply_content+='\n```sf-json\n'
                    reply_content+=json.dumps({'logid':logid})
                    reply_content+='\n```\n'
                    return reply_content

            teammode = int(context.get('teammode','0'))
            teambotkeep = int(context.get('teambotkeep','0'))
            # team-bot or assistant-bot
            teamid = int(context.get('teamid','0'))
            teambotid = int(context.get('teambotid','0'))

            if user_asst and (teamid == 0 or teambotid == 0):
                user_asst = None
            if user_asst:
                teambot_key = "sfteam:user:{}:team:{}:bot:{}".format(user_asst,teamid,teambotid)
                if myredis.redis.exists(teambot_key):
                    teammode = 2
                    teambot_name = myredis.redis.hget(teambot_key, 'name').decode().strip()
                    teambot_desc = myredis.redis.hget(teambot_key, 'desc').decode().strip()
                    teambot_prompt = myredis.redis.hget(teambot_key, 'prompt').decode().strip()
                    teambot_model = myredis.redis.hget(teambot_key, 'model')
                    teambot_nokb = 1
                    if teambot_nokb > 0:
                        fwd = 1
                else:
                    user_asst = None

            if teammode == 1:
                if teambotkeep == 2:
                    teambotkeep = 1
                    starter = myredis.ft_search(embedded_query=query_embedding,
                                                vector_field="text_vector",
                                                hybrid_fields=myredis.create_hybrid_field1(orgnum, user_flag, "category", "starter"),
                                                k=1)
                    if len(starter) > 0 and float(starter[0].vector_score) < 0.15:
                        teambotid = int(myredis.redis.hget(starter[0].id, 'teambotid').decode())
                if teambotkeep == 1 and teambotid == 0:
                    teambotkeep = 0
                if teambotkeep == 0:
                    newteambot, newteam = self.find_teambot(user_flag, from_org_id, from_chatbot_id, teamid, query)
                    if newteambot > 0:
                        teamid = newteam
                        teambotid = newteambot
                    else:
                        if teambotid == 0:
                            teammode = 0
                else:
                    if teamid == 0 and teambotid > 0:
                        teambot_pattern = "sfteam:org:{}:team:*:bot:{}".format(orgnum,teambotid)
                        keys_matched = myredis.redis.keys(teambot_pattern)
                        for key in keys_matched:
                            teambot_key=key.decode()
                            teamid=int(teambot_key.split(':')[4])

            if teammode == 1:
                teambot_key = "sfteam:org:{}:team:{}:bot:{}".format(orgnum,teamid,teambotid)
                log.info("[CHATGPT] key={} query={}".format(teambot_key,query))
                if myredis.redis.exists(teambot_key):
                    teambot_name = myredis.redis.hget(teambot_key, 'name').decode().strip()
                    teambot_desc = myredis.redis.hget(teambot_key, 'desc').decode().strip()
                    teambot_prompt = myredis.redis.hget(teambot_key, 'prompt').decode().strip()
                    teambot_model = myredis.redis.hget(teambot_key, 'model')
                    teambot_nokb = myredis.redis.hget(teambot_key, 'nokb')
                    if teambot_nokb is not None:
                        teambot_nokb = int(teambot_nokb.decode().strip())
                    else:
                        teambot_nokb = 0
                    if teambot_nokb > 0:
                        fwd = 1
                else:
                    teammode = 0
            if teammode == 0:
                teamid = 0
                teambotid = 0
            if teammode > 0:
                # "Do not try to answer the queries that are irrelevant to your functionality and responsibility, just reject them politely.\n"
                teambot_instruction = (
                    f"You are {teambot_name}.\n{teambot_desc}.\n"
                    "You only provide clear, concise, factual answers to queries, and do not try to make up an answer.\n"
                    "Your functionality and responsibility are described below, separated by 3 backticks.\n\n"
                    f"```\n{teambot_prompt}\n```\n"
                )
                character_id = f"x{teambotid}"
                character_desc = teambot_instruction
                if sfmodel is None and teambot_model is not None:
                    sfmodel = teambot_model.decode().strip()
                log.info("[CHATGPT] {} character id={} desc={}".format('asstbot' if user_asst else 'teambot',character_id,character_desc))
            else:
                sfbot_key = "sfbot:org:{}:bot:{}".format(orgnum,botnum)
                sfbot_model = myredis.redis.hget(sfbot_key, 'model')
                baidu_key = myredis.redis.hget(sfbot_key, 'baidu_key')
                google_key = myredis.redis.hget(sfbot_key, 'google_key')
                if sfmodel is None and sfbot_model is not None:
                    sfmodel = sfbot_model.decode().strip()
                if baidu_key is not None:
                    baidu_key = baidu_key.decode().strip()
                    if isinstance(baidu_key, str) and len(baidu_key) > 0:
                        llm_provider = 'baidu'
                        llm_credential = baidu_key
                if google_key is not None:
                    google_key = google_key.decode().strip()
                    if isinstance(google_key, str) and len(google_key) > 0:
                        llm_provider = 'google'
                        llm_credential = google_key

            commands = []
            atcs = myredis.ft_search(embedded_query=query_embedding,
                                     vector_field="text_vector",
                                     hybrid_fields=myredis.create_hybrid_field1(orgnum, user_flag, "category", "atc"),
                                     k=3)
            if len(atcs) > 0:
                for i, atc in enumerate(atcs):
                    if float(atc.vector_score) > 0.15:
                        break
                    cid = myredis.redis.hget(atc.id, 'id').decode()
                    csf = 1.0 - float(atc.vector_score)
                    commands.append({'id':cid,'category':"actionTransformer",'score':csf})

            new_query, hitdocs, refurls, similarity, use_faiss = Session.build_session_query(query, from_user_id, from_org_id, from_chatbot_id, user_flag, character_desc, character_id, user_asst, website, email, fwd, ctx)
            if new_query is None:
                return 'Sorry, I have no ideas about what you said.'

            log.info("[CHATGPT] session query={}".format(new_query))
            if new_query[-1]['role'] == 'assistant':
                reply_message = new_query.pop()
                reply_content = reply_message['content']
                logid = Session.save_session(query, reply_content, from_user_id, from_org_id, from_chatbot_id, sfuserid, 'auto', 0, 0, 0, similarity, use_faiss)
                reply_content = run_word_filter(reply_content, get_org_id(from_org_id))
                reply_content+='\n```sf-json\n'
                reply_content+=json.dumps({'logid':logid})
                reply_content+='\n```\n'
                return reply_content

            # if context.get('stream'):
            #     # reply in stream
            #     return self.reply_text_stream(query, new_query, from_user_id)

            reply_content, logid = self.reply_text(new_query, query, llm_provider, llm_credential, sfmodel, from_user_id, from_org_id, from_chatbot_id, sfuserid, similarity, temperature, use_faiss, 0)
            reply_embedding = oai_embeddings.embed_query(reply_content)
            docs = myredis.ft_search(embedded_query=reply_embedding,
                                     vector_field="text_vector",
                                     hybrid_fields=myredis.create_hybrid_field2(orgnum, botnum, user_flag, "category", "kb"),
                                     k=1)
            score = 0.0
            if len(docs) > 0:
                score = 1.0 - float(docs[0].vector_score)

            qnts = myredis.ft_search(embedded_query=query_embedding, vector_field="text_vector", hybrid_fields=myredis.create_hybrid_field(orgnum, "category", "qnt"), k=3)
            if len(qnts) > 0:
                for i, qnt in enumerate(qnts):
                    log.info(f"{i}) {qnt.id} {qnt.orgid} {qnt.category} {qnt.vector_score}")
                    if float(qnt.vector_score) > 0.2:
                        break
                    rid = myredis.redis.hget(qnt.id, 'id').decode()
                    send_query_notification(rid, query, reply_content)

            resources = []
            if nres > 0:
                resources = Session.get_resources(reply_content, from_user_id, from_org_id)
                reply_content = Session.insert_resource_to_reply(reply_content, from_user_id, from_org_id)
            reply_content = run_word_filter(reply_content, get_org_id(from_org_id))
            reply_content+='\n```sf-json\n'
            reply_content+=json.dumps({'docs':hitdocs,'pages':refurls,'resources':resources,'commands':commands,'score':score,'logid':logid,'teammode':teammode,'teamid':teamid,'teambotid':teambotid})
            reply_content+='\n```\n'
            #log.debug("[CHATGPT] user={}, query={}, reply={}".format(from_user_id, new_query, reply_content))
            return reply_content

        elif context.get('type', None) == 'IMAGE_CREATE':
            #return self.create_img(query, 0)
            imgdata = self.create_img(query, 0)
            reply_content=f"Image prompt: {query}\n"
            reply_content+='\n```sf-json\n'
            reply_content+=json.dumps({'images':imgdata})
            reply_content+='\n```\n'
            return reply_content

    def find_teambot(self, user_flag, org_id, chatbot_id, team_id, query):
        myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
        orgnum = get_org_id(org_id)
        botnum = get_bot_id(chatbot_id)
        team_info = '# Team Information\n'
        team_keys = []
        team_pattern = "sfteam:org:{}:team:*:data".format(orgnum)
        keys_matched = myredis.redis.keys(team_pattern)
        for key in keys_matched:
            team_keys.append(key.decode())
        if team_id > 0:
            team_key = "sfteam:org:{}:team:{}:data".format(orgnum,team_id)
            if team_key in team_keys:
                team_keys.clear()
                team_keys.append(team_key)
        for key in team_keys:
            team_desc = myredis.redis.hget(key, 'team_desc').decode()
            team_publ = 1
            fpub = myredis.redis.hget(key, 'public')
            if fpub is not None:
                team_publ = int(fpub.decode())
            if team_publ == 1:
                team_info += team_desc+'\n'
            else:
                if user_flag == 'internal':
                    team_info += team_desc+'\n'
        if len(team_info) < 20:
            log.info("[CHATGPT] find_teambot: No available team {}/{}".format(org_id,user_flag))
            return 0, 0
        sys_msg = (
            "You are a contact-center manager, and you try to dispatch the user query to the most suitable team/agent.\n"
            "You only provide clear, concise, factual answers to queries, and do not try to make up an answer.\n"
            "The functionality and responsibility of teams are described below in markdown format.\n\n"
            f"```markdown\n{team_info}\n```\n"
        )
        usr_msg = (
            "Here is user query.\n"
            f"```\n{query}\n```\n\n"
            "Reply the dispatchment in json format with 2 keys named team_id and agent_id.\n"
            "If you have no idea about how to dispatch based on the given team information, simply return team_id=0 and agent_id=0.\n"
            "The answer should be only json string and nothing else.\n"
        )
        msgs = [{'role':'system','content':sys_msg},{'role':'user','content':usr_msg}]
        try:
            use_azure = True if orgnum==4 else False
            if use_azure:
                response = azurec.chat.completions.create(
                    model="base",
                    messages=msgs,
                    temperature=0.1,
                    frequency_penalty=0.0,
                    presence_penalty=0.0,
                )
            else:
                response = client.chat.completions.create(
                    model=model_conf(const.OPEN_AI).get("model") or "gpt-4o-mini",
                    messages=msgs,
                    temperature=0.1,
                    frequency_penalty=0.0,
                    presence_penalty=0.0,
                )
            reply_content = response.choices[0].message.content
            reply_usage = response.usage
            log.info("[CHATGPT] find_teambot: result={} usage={}".format(reply_content,reply_usage))
            dispatch = json.loads(reply_content)
            return int(dispatch['agent_id']), int(dispatch['team_id'])
        except Exception as e:
            log.exception(e)
            return 0, 0

    def gpt_stream(self, data):
        #for i in range(10): yield f"Data: {i}\n" time.sleep(0.5)
        try:
            reqid = uuid.uuid4()
            query = data["msg"]
            session_id = data["id"]
            from_org_id = data["orgid"]
            from_org_id, from_chatbot_id = get_org_bot(from_org_id)
            nres = int(data.get("res", 0))
            fwd = int(data.get("fwd", 0))
            ctx = int(data.get("ctx", 0))
            sfmodel = data.get("model", 'undef')
            user_flag = data.get("userflag", 'external')
            character_id = data.get("character_id")
            if character_id is not None:
                character_id = f"c{character_id}"
            character_desc = data.get("character_desc", 'undef')
            temperature = data.get("temperature", 'undef')
            sfuserid = data.get("sfuserid", 'undef')
            if not (isinstance(sfmodel, str) and (sfmodel.startswith('ft:') or sfmodel.startswith('gpt-'))):
                sfmodel = model_conf(const.OPEN_AI).get("model", "gpt-4o-mini")
            try:
                temperature = float(temperature)
                if temperature < 0.0 or temperature > 1.0:
                    raise ValueError()
            except ValueError:
                temperature = model_conf(const.OPEN_AI).get("temperature", 0.75)
            if not (isinstance(character_desc, str) and character_desc!='undef' and len(character_desc)>0):
                character_desc = "You are a helpful assistant."
            log.info("[CHATGPT|stream] model={} temperature={}".format(sfmodel, temperature))
            log.info("[CHATGPT|stream] character={}".format(character_desc))
            log.info("[CHATGPT|stream] query={}".format(query))
            #new_query, hitdocs, refurls, similarity, use_faiss = Session.build_session_query(query, session_id,
            #from_org_id, from_chatbot_id, user_flag, character_desc, character_id, None, None, None, fwd, ctx)
            new_query = [{ "role": "system", "content": character_desc }, { "role": "user", "content": query }]
            mtx = ""
            response = client.chat.completions.create(
                messages=new_query,
                model=sfmodel,
                temperature=temperature,
                #max_tokens=context_tokens,
                stream=True,
                stream_options={"include_usage":True},
            )
            for chunk in response:
                if len(chunk.choices)>0:
                    chunk.id = reqid
                    del chunk.object
                    del chunk.system_fingerprint
                    del chunk.model
                    if chunk.usage is None:
                        del chunk.usage
                    if chunk.choices[0].logprobs is None:
                        del chunk.choices[0].logprobs
                    if chunk.choices[0].finish_reason is None:
                        del chunk.choices[0].finish_reason
                    if chunk.choices[0].delta.function_call is None:
                        del chunk.choices[0].delta.function_call
                    if chunk.choices[0].delta.role is None:
                        del chunk.choices[0].delta.role
                    if chunk.choices[0].delta.tool_calls is None:
                        del chunk.choices[0].delta.tool_calls
                    if isinstance(chunk.choices[0].delta.content, str):
                        mtx += chunk.choices[0].delta.content
                    yield 'data: '+chunk.model_dump_json()+'\n\n'
                else:
                    if chunk.usage:
                        log.info("[CHATGPT|stream][{}] usage={}", chunk.model, chunk.usage)
                        model_name = chunk.model
                        used_tokens = chunk.usage.total_tokens
                        prompt_tokens = chunk.usage.prompt_tokens
                        completion_tokens = chunk.usage.completion_tokens
                        logid = Session.save_session(query, mtx, session_id, from_org_id, from_chatbot_id, sfuserid, model_name, used_tokens, prompt_tokens, completion_tokens)
                        log.info("[CHATGPT|stream] logid={}".format(logid))
            yield 'data: [DONE]\n\n'
        except Exception as e:
            log.exception(e)
            yield 'data: [DONE]\n\n'

    def reply_text(self, query, qtext, llm_provider, llm_credential, sfmodel, user_id, org_id, chatbot_id, sfuserid, similarity, temperature, use_faiss=False, retry_count=0):
        try:
            try:
                temperature = float(temperature)
                if temperature < 0.0 or temperature > 1.0:
                    raise ValueError()
            except ValueError:
                temperature = model_conf(const.OPEN_AI).get("temperature", 0.75)

            messages = []
            for msg in query:
                if msg['role']=='system':
                    messages.append(SystemMessage(content=msg['content']))
                elif msg['role']=='user':
                    messages.append(HumanMessage(content=msg['content']))
                elif msg['role']=='assistant':
                    messages.append(AIMessage(content=msg['content']))
            log.info("[LLM] provider={}", llm_provider)
            result = None
            if llm_provider=="baidu": # ERNIE-3.5-8K
                llmqfn = QianfanChatEndpoint(model="ERNIE-Lite-8K", qianfan_ak=qfn_ak, qianfan_sk=qfn_sk, streaming=True,)
                result = llmqfn.invoke(messages, **{"temperature": temperature})
                reply_content = result.content
                model_name = "ERNIE-Lite-8K"
                used_tokens = 0
                prompt_tokens = 0
                completion_tokens = 0
                log.info("[LLM/Qianfan] usage={}", 0)
                log.info("[LLM/Qianfan] reply={}", reply_content)
                logid = Session.save_session(qtext, reply_content, user_id, org_id, chatbot_id, sfuserid, model_name, used_tokens, prompt_tokens, completion_tokens, similarity, use_faiss)
                return reply_content, logid
            elif llm_provider=="google":
                llmgmn = ChatGoogleGenerativeAI(model="gemini-pro", google_api_key=gmn_key, temperature=temperature, convert_system_message_to_human=True)
                result = llmgmn.invoke(messages)
                reply_content = result.content
                model_name = "gemini-pro"
                used_tokens = 0
                prompt_tokens = 0
                completion_tokens = 0
                log.info("[LLM/Gemini] usage={}", 0)
                log.info("[LLM/Gemini] reply={}", reply_content)
                logid = Session.save_session(qtext, reply_content, user_id, org_id, chatbot_id, sfuserid, model_name, used_tokens, prompt_tokens, completion_tokens, similarity, use_faiss)
                return reply_content, logid

            orgnum = get_org_id(org_id)
            use_azure = True if orgnum==4 else False
            if use_azure:
                response = azurec.chat.completions.create(
                    model="base",
                    messages=query,
                    temperature=temperature,
                    frequency_penalty=model_conf(const.OPEN_AI).get("frequency_penalty", 0.0),
                    presence_penalty=model_conf(const.OPEN_AI).get("presence_penalty", 1.0),
            )
            else:
                response = client.chat.completions.create(
                    model=sfmodel or model_conf(const.OPEN_AI).get("model") or "gpt-4o-mini",
                    messages=query,
                    temperature=temperature,  # 熵值，在[0,1]之间，越大表示选取的候选词越随机，回复越具有不确定性，建议和top_p参数二选一使用，创意性任务越大越好，精确性任务越小越好
                    #max_tokens=context_tokens,
                    #top_p=model_conf(const.OPEN_AI).get("top_p", 0.7),,  #候选词列表。0.7 意味着只考虑前70%候选词的标记，建议和temperature参数二选一使用
                    frequency_penalty=model_conf(const.OPEN_AI).get("frequency_penalty", 0.0),  # [-2,2]之间，该值越大则越降低模型一行中的重复用词，更倾向于产生不同的内容
                    presence_penalty=model_conf(const.OPEN_AI).get("presence_penalty", 1.0),  # [-2,2]之间，该值越大则越不受输入限制，将鼓励模型生成输入中不存在的新词，更倾向于产生不同的内容
                )
            reply_content = response.choices[0].message.content
            model_name = response.model
            used_tokens = response.usage.total_tokens
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            log.debug(response)
            log.info("[{}] usage={}", response.model, response.usage)
            log.info("[CHATGPT] reply={}", reply_content)
            logid = Session.save_session(qtext, reply_content, user_id, org_id, chatbot_id, sfuserid, model_name, used_tokens, prompt_tokens, completion_tokens, similarity, use_faiss)
            return reply_content, logid
        except openai.RateLimitError as e:
            # rate limit exception
            log.warn(e)
            if retry_count < 1:
                time.sleep(5)
                log.warn("[CHATGPT] RateLimit exceed, retry {} attempts".format(retry_count+1))
                return self.reply_text(query, qtext, llm_provider, llm_credential, sfmodel, user_id, org_id, chatbot_id, sfuserid, similarity, temperature, use_faiss, retry_count+1)
            else:
                return "You're asking too quickly, please take a break before asking me again.", None
        except openai.APIConnectionError as e:
            log.warn(e)
            log.warn("[CHATGPT] APIConnection failed")
            return "I can't connect to the service, please try again later.", None
        except openai.APITimeoutError as e:
            log.warn(e)
            log.warn("[CHATGPT] Timeout")
            return "I haven't received the message, please try again later.", None
        except openai.InternalServerError as e:
            log.warn(e)
            log.warn("[CHATGPT] Service Unavailable")
            return "The server is overloaded or not ready yet.", None
        except openai.BadRequestError as e:
            log.warn(e)
            log.warn("[CHATGPT] Bad Request")
            return e.body.get('message', 'Bad request'), None
        except Exception as e:
            # unknown exception
            log.exception(e)
            Session.clear_session(user_id)
            return "I'm unable to answer your question right now. Please try again later.", None

    async def reply_text_stream(self, query, context, retry_count=0):
        try:
            log.info("[CHATGPT] query={}".format(query))
            from_user_id = context['from_user_id']
            from_org_id = context['from_org_id']
            from_org_id, from_chatbot_id = get_org_bot(from_org_id)
            user_flag = context['userflag']
            user_uuid = context.get('userid','undef')
            user_asst = context.get('userasst','undef')
            file_ids = context.get('fileids','0')
            nres = int(context.get('res','0'))
            fwd = int(context.get('fwd','0'))
            ctx = int(context.get('ctx','0'))
            character_id = context.get('character_id')
            character_desc = context.get('character_desc')
            temperature = context['temperature']
            website = context.get('website','undef')
            email = context.get('email','undef')
            sfmodel = context.get('sfmodel','undef')
            sfuserid = context.get('sfuserid','undef')
            if not is_valid_uuid(user_uuid):
                user_uuid = None
            if not is_valid_uuid(user_asst):
                user_asst = None
            if not (isinstance(sfmodel, str) and (sfmodel.startswith('ft:') or sfmodel.startswith('gpt-'))):
                sfmodel = None

            if isinstance(file_ids, str) and file_ids != '0' and from_chatbot_id.startswith('user:'):
                character_id = file_ids
                from_chatbot_id = 'bot:file'
                user_flag = 'internal'
                user_uuid = None
                user_asst = None
                website = None
                email = None
                nres = 0
                fwd = 0
                ctx = 0

            new_query, hitdocs, refurls, similarity, use_faiss = Session.build_session_query(query, from_user_id, from_org_id, from_chatbot_id, user_flag, character_desc, character_id, user_asst, website, email, fwd, ctx)
            if new_query is None:
                yield True,'Sorry, I have no ideas about what you said.'

            log.info("[CHATGPT] session query={}".format(new_query))
            if new_query[-1]['role'] == 'assistant':
                reply_message = new_query.pop()
                reply_content = reply_message['content']
                logid = Session.save_session(query, reply_content, from_user_id, from_org_id, from_chatbot_id, sfuserid, 'auto', 0, 0, 0, similarity, use_faiss)
                reply_content = run_word_filter(reply_content, get_org_id(from_org_id))
                reply_content+='\n```sf-json\n'
                reply_content+=json.dumps({'logid':logid})
                reply_content+='\n```\n'
                yield True,reply_content

            try:
                temperature = float(temperature)
                if temperature < 0.0 or temperature > 1.0:
                    raise ValueError()
            except ValueError:
                temperature = model_conf(const.OPEN_AI).get("temperature", 0.75)

            orgnum = str(get_org_id(from_org_id))
            botnum = str(get_bot_id(from_chatbot_id))
            myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
            sfbot_key = "sfbot:org:{}:bot:{}".format(orgnum,botnum)
            sfbot_model = myredis.redis.hget(sfbot_key, 'model')
            if sfmodel is None and sfbot_model is not None:
                sfmodel = sfbot_model.decode().strip()

            res = client.chat.completions.create(
                model=sfmodel or model_conf(const.OPEN_AI).get("model") or "gpt-4o-mini",
                messages=new_query,
                temperature=temperature,  # 熵值，在[0,1]之间，越大表示选取的候选词越随机，回复越具有不确定性，建议和top_p参数二选一使用，创意性任务越大越好，精确性任务越小越好
                #max_tokens=context_tokens,
                #top_p=model_conf(const.OPEN_AI).get("top_p", 0.7),,  #候选词列表。0.7 意味着只考虑前70%候选词的标记，建议和temperature参数二选一使用
                frequency_penalty=model_conf(const.OPEN_AI).get("frequency_penalty", 0.0),  # [-2,2]之间，该值越大则越降低模型一行中的重复用词，更倾向于产生不同的内容
                presence_penalty=model_conf(const.OPEN_AI).get("presence_penalty", 1.0),  # [-2,2]之间，该值越大则越不受输入限制，将鼓励模型生成输入中不存在的新词，更倾向于产生不同的内容
                stream=True,
                stream_options={"include_usage":True},
            )
            full_response = ""
            for chunk in res:
                log.debug(chunk)
                if chunk.usage:
                    log.info("[CHATGPT|stream][{}] usage={}", chunk.model, chunk.usage)
                    model_name = chunk.model
                    used_tokens = chunk.usage.total_tokens
                    prompt_tokens = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens
                if chunk.choices[0].finish_reason=="stop":
                    break
                chunk_message = chunk.choices[0].delta.content
                if(chunk_message):
                    full_response+=chunk_message
                yield False,full_response
            """
            prompt_tokens = num_tokens_from_messages(new_query)
            completion_tokens = num_tokens_from_string(full_response)
            used_tokens = prompt_tokens + completion_tokens
            """
            logid = Session.save_session(query, full_response, from_user_id, from_org_id, from_chatbot_id, sfuserid, model_name, used_tokens, prompt_tokens, completion_tokens, similarity, use_faiss)

            resources = []
            if nres > 0:
                resources = Session.get_resources(full_response, from_user_id, from_org_id)

            full_response = run_word_filter(full_response, get_org_id(from_org_id))
            full_response+='\n```sf-json\n'
            full_response+=json.dumps({'docs':hitdocs,'pages':refurls,'resources':resources,'logid':logid})
            full_response+='\n```\n'
            #log.debug("[CHATGPT] user={}, query={}, reply={}".format(from_user_id, new_query, full_response))
            yield True,full_response

        except openai.RateLimitError as e:
            # rate limit exception
            log.warn(e)
            if retry_count < 1:
                time.sleep(5)
                log.warn("[CHATGPT] RateLimit exceed, retry {} attempts".format(retry_count+1))
                yield True, self.reply_text_stream(query, context, retry_count+1)
            else:
                yield True, "You're asking too quickly, please take a break before asking me again."
        except openai.APIConnectionError as e:
            log.warn(e)
            log.warn("[CHATGPT] APIConnection failed")
            yield True, "I can't connect to the service, please try again later."
        except openai.APITimeoutError as e:
            log.warn(e)
            log.warn("[CHATGPT] Timeout")
            yield True, "I haven't received the message, please try again later."
        except openai.InternalServerError as e:
            log.warn(e)
            log.warn("[CHATGPT] Service Unavailable")
            yield True, "The server is overloaded or not ready yet."
        except openai.BadRequestError as e:
            log.warn(e)
            log.warn("[CHATGPT] Bad Request")
            yield True, e.body.get('message', 'Bad request')
        except Exception as e:
            # unknown exception
            log.exception(e)
            Session.clear_session(from_user_id)
            yield True, "I'm unable to answer your question right now. Please try again later."

    def create_img(self, query, retry_count=0):
        try:
            log.info("[DALLE] image_query={}".format(query))
            response = client.images.generate(
                model="dall-e-3",
                size="1024x1024",
                quality="standard",
                response_format="b64_json",
                prompt=query,
                n=1,
            )
            image_data = response.data[0].model_dump()["b64_json"]
            image_obj = Image.open(BytesIO(base64.b64decode(image_data)))
            random_string = generate_random_string(8)
            image_path = f"/tmp/{random_string}.jpg"
            image_obj.save(image_path)
            current_utc_time = datetime.utcnow()
            formatted_date = current_utc_time.strftime("%Y/%m/%d")
            object_key = f"dalle/{formatted_date}/{random_string}.jpg"
            alioss_ak = common_conf_val("aliyun_oss_ak", "xxx")
            alioss_sk = common_conf_val("aliyun_oss_sk", "xxx")
            alioss_ep = common_conf_val("aliyun_oss_endpoint", "xxx")
            alioss_bk = common_conf_val("aliyun_oss_bucket", "xxx")
            sfoss_url = common_conf_val("sflow_oss_url", "xxx")
            auth = oss2.Auth(alioss_ak, alioss_sk)
            bucket = oss2.Bucket(auth, alioss_ep, alioss_bk)
            bucket.put_object_from_file(object_key, image_path)
            bucket.put_object_acl(object_key, oss2.OBJECT_ACL_PUBLIC_READ)
            if os.path.exists(image_path):
                os.remove(image_path)
            #image_url = response.data[0].url
            image_url = f"{sfoss_url}/{object_key}"
            log.info("[DALLE] image_url={}".format(image_url))
            return [image_url]
        except openai.RateLimitError as e:
            log.warn(e)
            if retry_count < 1:
                time.sleep(5)
                log.warn("[DALLE] ImgCreate RateLimit exceed, retry {} attempts".format(retry_count+1))
                return self.create_img(query, retry_count+1)
            else:
                return "You're asking too quickly, please take a break before asking me again."
        except Exception as e:
            log.exception(e)
            return None


class Session(object):
    @staticmethod
    def build_session_query(query, user_id, org_id, chatbot_id='bot:0', user_flag='external', character_desc=None, character_id=None, user_uuid=None, website=None, email=None, fwd=0, ctx=0):
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
        config_prompt = common_conf_val("input_prompt", "")
        max_history_num = model_conf(const.OPEN_AI).get('max_history_num', None)

        if user_id not in user_session:
            user_session[user_id] = []
        session = user_session.get(user_id)
        query_tokens = num_tokens_from_string(query)
        if query_tokens >= context_tokens/2:
            Session.clear_session(user_id)

        faiss_id = user_id
        if isinstance(website, str) and website != 'undef' and len(website) > 0:
            log.info("[FAISS] try to search data of website:{}".format(website))
            faiss_id = calculate_md5('website:'+re.sub(r'https?://','',website.lower()))
        elif isinstance(email, str) and email != 'undef' and len(email) > 0:
            log.info("[FAISS] try to search data of email:{}".format(email))
            faiss_id = calculate_md5(email.lower())

        if re.match(md5sum_pattern, faiss_id):
            log.info("[FAISS] try to load local store {}".format(faiss_id))
        if re.match(md5sum_pattern, faiss_id) and os.path.exists(f"{faiss_store_root}{faiss_id}"):
            faiss_store_path = f"{faiss_store_root}{faiss_id}"
            dbx = FAISS.load_local(faiss_store_path, oai_embeddings)
            log.info("[FAISS] local store loaded")
            similarity = 0.0
            docs = dbx.similarity_search_with_score(query, k=3)
            log.info("[FAISS] semantic search done")
            if len(docs) == 0:
                log.info("[FAISS] semantic search: None")
                return None, [], [], similarity, True
            similarity = float(docs[0][1])
            '''
            if len(docs) > 0 and similarity < 0.6:
                log.info(f"[FAISS] semantic search: score:{similarity} < threshold:0.6")
                return None, [], [], similarity, True
            '''
            system_prompt = 'You are answering the question just like you are the owner or partner of the company described in the context.'
            if isinstance(character_desc, str) and character_desc != 'undef' and len(character_desc) > 0:
                system_prompt = character_desc
            system_prompt += '\nIf you don\'t know the answer, just say you don\'t know. DO NOT try to make up an answer.'
            # system_prompt += '\nIf the question is not related to the context, politely respond that you are tuned to only answer questions that are related to the context.'
            system_prompt += '\nIf you are unclear about the question, politely respond that you need a clearer and more detailed description.'
            system_prompt += f"\n{config_prompt}\n```"
            for doc, score in docs:
                log.info("[FAISS] {} {}".format(score, json.dumps(doc.metadata)))
                '''
                if score < 0.6:
                    break
                '''
                system_prompt += '\n' + doc.page_content
            system_prompt += '\n```\n'
            log.info("[FAISS] prompt={}".format(system_prompt))
            system_item = {'role': 'system', 'content': system_prompt}
            user_item = {'role': 'user', 'content': query}
            session.clear()
            session.append(system_item)
            session.append(user_item)
            return session, [], [], similarity, True

        file_chat = False
        if chatbot_id == 'bot:file':
            file_chat = True
            chatbot_id = 'bot:0'

        orgnum = get_org_id(org_id)
        qnaorg = "(0|{})".format(orgnum)
        botnum = str(get_bot_id(chatbot_id))
        if isinstance(character_id, str) and (character_id[0] == 'c' or character_id[0] == 'x' or character_id[0] == 't'):
            botnum += " | {}".format(character_id)
        if user_uuid:
            botnum = str(character_id)
        if file_chat:
            botnum = str(character_id)
        refurls = []
        hitdocs = []
        qna_output = None
        myquery = oai_embeddings.embed_query(query)
        myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
        qnas = myredis.ft_search(embedded_query=myquery, vector_field="title_vector", hybrid_fields=myredis.create_hybrid_field(qnaorg, "category", "qa"))
        if file_chat:
            qnas = []
        if len(qnas) > 0 and float(qnas[0].vector_score) < 0.15:
            qna = qnas[0]
            log.info(f"Q/A: {qna.id} {qna.orgid} {qna.category} {qna.vector_score}")
            try:
                qnatext = myredis.redis.hget(qna.id, 'text').decode()
                answers = json.loads(qnatext)
                if len(answers)>0:
                    qna_output = random.choice(answers)
                    fid = myredis.redis.hget(qna.id, 'id').decode()
                    increase_hit_count(fid, 'qa', '')
            except json.JSONDecodeError as e:
                pass
            except Exception as e:
                pass

        log.info("[RDSFT] org={} {} {}".format(org_id, orgnum, qnaorg))
        if user_uuid:
            fc_key = "sftool:org:{}:action:{}".format(orgnum,39)
            if myredis.redis.exists(fc_key):
                fc_json = myredis.redis.hget(fc_key, 'fcjson').decode()
                fc_tools = []
                fc_tools.append({"type":"function","function":json.loads(fc_json)})
                fc_funcs = { "get_latest_news": get_latest_news, }
                fc_msgs = [
                    {'role':'system', 'content':"You are an intelligence agent, and you try to handle the query."},
                    {"role":"user", "content":"Today is "+datetime.now().strftime("%Y-%m-%d")+". "+query}
                ]
                fc_qcmp = client.chat.completions.create(model="gpt-4o-mini", messages=fc_msgs, tools=fc_tools, tool_choice="auto")
                fc_qrsp = fc_qcmp.choices[0].message
                if fc_qrsp.tool_calls is not None:
                    fc_msgs.append(fc_qrsp)
                    for tc in fc_qrsp.tool_calls:
                        if tc.type=="function":
                            function_name = tc.function.name
                            function_args = json.loads(tc.function.arguments)
                            function_tocall = fc_funcs[function_name]
                            function_output = function_tocall(**function_args)
                            fc_msgs.append({"tool_call_id":tc.id, "role":"tool", "name":function_name, "content":function_output})
                            query += f"\n\n```\n{function_output}\n```\n"
                    #fc_fcmp = client.chat.completions.create(model="gpt-4o-mini", messages=fc_msgs)
                    #fc_data = fc_fcmp.choices[0].message.content
                    #log.info("[FUNC] msgs={}", fc_msgs)
                    #log.info("[FUNC] data={}", fc_data)
                    #query += f"\n\n```\n{fc_data}\n```\n"

        similarity = 0.0
        docs = myredis.ft_search(embedded_query=myquery,
                                 vector_field="text_vector",
                                 hybrid_fields=myredis.create_hybrid_field2(str(orgnum), botnum, user_flag, "category", "ka" if file_chat else "kb"))

        system_prompt = 'You are a helpful AI customer support agent. Use the following pieces of context to answer the customer inquiry.'
        if file_chat:
            system_prompt = 'You are a helpful AI document assistant. Use the following pieces of context to answer user queries about relevant documents.'

        orgnum = str(get_org_id(org_id))
        botnum = str(get_bot_id(chatbot_id))
        sfbot_key = "sfbot:org:{}:bot:{}".format(orgnum,botnum)
        sfbot_threshold = myredis.redis.hget(sfbot_key, 'threshold')
        if sfbot_threshold is not None:
            sfbot_threshold = 1.0-int(sfbot_threshold.decode())/100
        else:
            sfbot_threshold = float(common_conf_val('similarity_threshold', 0.75))
        if len(docs) > 0:
            similarity = 1.0 - float(docs[0].vector_score)
            if similarity < sfbot_threshold:
                docs = []

        sfbot_char_desc = myredis.redis.hget(sfbot_key, 'character_desc')
        if sfbot_char_desc is not None:
            sfbot_char_desc = sfbot_char_desc.decode()
            if len(sfbot_char_desc) > 0:
                system_prompt = sfbot_char_desc
        if isinstance(character_desc, str) and character_desc != 'undef' and len(character_desc) > 0:
            system_prompt = character_desc

        if fwd > 0:
            log.info("[CHATGPT] prompt(onlyfwd)={}".format(system_prompt))
            if len(session) > 0 and session[0]['role'] == 'system':
                session.pop(0)
            if len(session) > ctx*2:
                del session[:len(session)-ctx*2]
            system_item = {'role': 'system', 'content': system_prompt}
            user_item = {'role': 'user', 'content': query}
            session.insert(0, system_item)
            session.append(user_item)
            while len(session) > 3 and num_tokens_from_messages(session) > context_tokens:
                del session[1:3]
            return session, [], [], similarity, False

        if isinstance(character_id, str) and character_id.startswith('x'):
            log.info("[CHATGPT] {} character id={} add context".format('asstbot' if user_uuid else 'teambot',character_id))
        else:
            system_prompt += '\nIf you don\'t know the answer, just say you don\'t know. DO NOT try to make up an answer.'
            # system_prompt += '\nIf the question is not related to the context, politely respond that you are tuned to only answer questions that are related to the context.'
            system_prompt += '\nIf you are unclear about the question, politely respond that you need a clearer and more detailed description.'

        if len(docs) == 0 and qna_output is None:
            log.info("[CHATGPT] prompt(nodoc)={}".format(system_prompt))
            if len(session) > 0 and session[0]['role'] == 'system':
                session.pop(0)
            if len(session) > ctx*2:
                del session[:len(session)-ctx*2]
            system_item = {'role': 'system', 'content': system_prompt}
            user_item = {'role': 'user', 'content': query}
            session.insert(0, system_item)
            session.append(user_item)
            while len(session) > 3 and num_tokens_from_messages(session) > context_tokens:
                del session[1:3]
            return session, [], [], similarity, False

        system_prompt += f"\n{config_prompt}\n```"
        if qna_output is not None:
            system_prompt += '\n' + qna_output
        for i, doc in enumerate(docs):
            log.info(f"{i}) {doc.id} {doc.orgid} {doc.category} {doc.vector_score}")
            if float(doc.vector_score) < sfbot_threshold:
                system_prompt += '\n' + myredis.redis.hget(doc.id, 'text').decode()
            if float(doc.vector_score) < 0.15:
                urlhit = ''
                docurl = myredis.redis.hget(doc.id, 'source')
                if docurl is not None:
                    urlhit = docurl.decode()
                dockey = myredis.redis.hget(doc.id, 'dkey')
                if dockey is not None:
                    dockey = dockey.decode()
                    dockeyparts = dockey.split(":")
                    fct = dockeyparts[1]
                    fid = dockeyparts[2]
                    if fct == 'file':
                        dfname = myredis.redis.hget(doc.id, 'filename')
                        if dfname is not None:
                            dfname = dfname.decode()
                        hitdocs.append({'id':fid,'category':fct,'url':urlhit,'filename':dfname,'key':f"{fid};{urlhit}"})
            if float(doc.vector_score) < 0.2:
                docurl = myredis.redis.hget(doc.id, 'source')
                if docurl is None:
                    continue
                urlkey = myredis.redis.hget(doc.id, 'refkey')
                if urlkey is None:
                    continue
                urltitle = None
                try:
                    docurl = docurl.decode()
                    urlkey = urlkey.decode()
                    urlmeta = json.loads(myredis.redis.lindex(urlkey, 0).decode())
                    urltitle = urlmeta['title']
                except json.JSONDecodeError as e:
                    log.info("Error decoding JSON: {} {}".format(urlkey, str(e)))
                except Exception as e:
                    log.info("Error URL: {} {}".format(urlkey, str(e)))
                log.info(f"{i}) {doc.id} URL={docurl} Title={urltitle}")
                refurls.append({'url': docurl, 'title': urltitle})
        system_prompt += '\n```\n'
        log.info("[CHATGPT] prompt={}".format(system_prompt))
        refurls = get_unique_by_key(refurls, 'url')
        hitdocs = get_unique_by_key(hitdocs, 'key')
        hitdocs = [{k: v for k, v in d.items() if k != 'key'} for d in hitdocs]
        if file_chat:
            hitdocs = []
        for doc in hitdocs:
            increase_hit_count(doc['id'], doc['category'], doc['url'])
        if len(session) > 0 and session[0]['role'] == 'system':
            session.pop(0)
        if len(session) > ctx*2:
            del session[:len(session)-ctx*2]
        system_item = {'role': 'system', 'content': system_prompt}
        user_item = {'role': 'user', 'content': query}
        session.insert(0, system_item)
        session.append(user_item)
        while len(session) > 3 and num_tokens_from_messages(session) > context_tokens:
            del session[1:3]
        return session, hitdocs, refurls, similarity, False

    @staticmethod
    def save_session(query, answer, user_id, org_id, chatbot_id, sfuserid="undef", model_name="auto", used_tokens=0, prompt_tokens=0, completion_tokens=0, similarity=0.0, use_faiss=False):
        session = user_session.get(user_id)
        if session:
            # append conversation
            gpt_item = {'role': 'assistant', 'content': answer}
            session.append(gpt_item)
        """
        max_tokens = model_conf(const.OPEN_AI).get('conversation_max_tokens')
        if not max_tokens or max_tokens > context_tokens:
            max_tokens = context_tokens
        if used_tokens > max_tokens and len(session) >= 3:
            session.pop(1)
            session.pop(1)
        """
        if use_faiss:
            return None

        orgnum = str(get_org_id(org_id))
        botnum = str(get_bot_id(chatbot_id))
        if used_tokens > 0:
            myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
            sfbot_key = "sfbot:org:{}:bot:{}".format(orgnum,botnum)
            momkey = 'stat_'+datetime.now().strftime("%Y%m")
            momqty = myredis.redis.hget(sfbot_key, momkey)
            if momqty is None:
                myredis.redis.hset(sfbot_key, momkey, 1)
            else:
                momqty = int(momqty.decode())
                myredis.redis.hset(sfbot_key, momkey, momqty+1)

        if botnum == '0':
            return None

        gqlurl = 'http://127.0.0.1:5000/graphql'
        gqlfunc = 'createChatHistory'
        headers = { "Content-Type": "application/json", }
        question = base64.b64encode(query.encode('utf-8')).decode('utf-8')
        answer = base64.b64encode(answer.encode('utf-8')).decode('utf-8')
        sfuidkv = ''
        if isinstance(sfuserid, str) and sfuserid != 'undef' and len(sfuserid) > 0:
            sfuidkv = f"userId:\"{sfuserid}\","
        xquery = f"""mutation {gqlfunc} {{ {gqlfunc}( chatHistory:{{ tag:"{user_id}",organizationId:{orgnum},sfbotId:{botnum},{sfuidkv}question:"{question}",answer:"{answer}",similarity:{similarity},model:"{model_name}",promptTokens:{prompt_tokens},completionTokens:{completion_tokens},totalTokens:{used_tokens}}}){{ id tag }} }}"""
        gqldata = { "query": xquery, "variables": {}, }
        try:
            gqlresp = requests.post(gqlurl, json=gqldata, headers=headers)
            log.info("[HISTORY] response: {} {}".format(gqlresp.status_code, gqlresp.text.strip()))
            if gqlresp.status_code != 200:
                return None
            chatlog = json.loads(gqlresp.text)
            return chatlog['data']['createChatHistory']['id']
        except Exception as e:
            log.exception(e)
            return None

    @staticmethod
    def clear_session(user_id):
        user_session[user_id] = []

    @staticmethod
    def get_resources(query, user_id, org_id):
        orgnum = get_org_id(org_id)
        resorg = "(0|{})".format(orgnum)
        myquery = oai_embeddings.embed_query(query)
        myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
        ress = myredis.ft_search(embedded_query=myquery, vector_field="text_vector", hybrid_fields=myredis.create_hybrid_field(resorg, "category", "res"), k=5)
        if len(ress) == 0:
            return []

        resources = []
        for i, res in enumerate(ress):
            resurl = myredis.redis.hget(res.id, 'url')
            resnam = myredis.redis.hget(res.id, 'title')
            vscore = 1.0 - float(res.vector_score)
            if resurl is not None:
                resurl = resurl.decode()
                resnam = resnam.decode()
                resources.append({'url':resurl,'name':resnam,'score':vscore})
        resources = get_unique_by_key(resources, 'url')
        return resources

    @staticmethod
    def get_top_resource(query, user_id, org_id, pos=0):
        orgnum = get_org_id(org_id)
        resorg = "(0|{})".format(orgnum)
        myquery = oai_embeddings.embed_query(query)
        myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
        ress = myredis.ft_search(embedded_query=myquery, vector_field="text_vector", hybrid_fields=myredis.create_hybrid_field(resorg, "category", "res"), k=1, offset=pos)
        if len(ress) == 0:
            return None
        res0 = ress[0]
        if float(res0.vector_score) > 0.25:
            return None
        resurl = myredis.redis.hget(res0.id, 'url')
        if resurl is None:
            return None
        resurl = resurl.decode()
        resname = myredis.redis.hget(res0.id, 'title')
        vscore = 1.0 - float(res0.vector_score)
        if resname is not None:
            resname = resname.decode()
        urlnoq = remove_url_query(resurl)
        restype = 'unknown'
        if is_image_url(urlnoq):
            restype = 'image'
        elif is_video_url(urlnoq):
            restype = 'video'
        topres = {'rid':res0.id, 'url':resurl,'name':resname,'type':restype,'score':vscore}
        return topres

    @staticmethod
    def insert_resource_to_reply(text, user_id, org_id):
        resrids=set()
        paragraphs = text.split("\n\n")
        for i, paragraph in enumerate(paragraphs):
            if len(paragraph) < 50:
                continue
            found = False
            for j in range(10):
                resource = Session.get_top_resource(paragraph, user_id, org_id, j)
                if resource is None:
                    found = False
                    break
                resrid = resource['rid']
                if resrid not in resrids:
                    found = True
                    resrids.add(resrid)
                    break
            if not found:
                continue
            resurl = resource['url']
            resname = resource['name']
            restype = resource['type']
            if restype == 'image':
                imagetag = f"\n\n<img src=\"{resurl}\" alt=\"{resname}\" width=\"600\">\n\n\n"
                paragraphs[i] = paragraphs[i] + imagetag
            elif restype == 'video':
                videotag = f"\n\n<video width=\"600\" controls><source src=\"{resurl}\" type=\"video/mp4\">Your browser does not support the video tag.</video>\n\n\n"
                paragraphs[i] = paragraphs[i] + videotag
        modified_text = "\n\n".join(paragraphs)
        return modified_text
