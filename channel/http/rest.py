# encoding:utf-8

import datetime
import re
import time
import json
import hashlib
from flask import jsonify, request
from common import const
from common.redis import RedisSingleton
from config import model_conf, common_conf_val
from common import log
from langchain_openai import OpenAIEmbeddings

def calculate_md5(string):
    md5_hash = hashlib.md5()
    md5_hash.update(string.encode('utf-8'))
    return md5_hash.hexdigest()

def is_valid_uuid(uuidstr):
    pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    return bool(re.match(pattern, uuidstr, re.IGNORECASE))

class Rest():
    def __init__(self, agent):
        self.agent = agent
        super(Rest, self).__init__()

    @staticmethod
    def somefunc(myvar):
        try:
            return "Done"
        except Exception as e:
            return 'Failed to run somefunc'

def filesearch(request):
    try:
        if (request is None):
            log.info("rest: Null Request")
            return False, None
        data = json.loads(request.data)
        query = data.get("query")
        if (query is None):
            log.info("rest: Null Query")
            return False, None

        orgid = data.get("orgid", 0)
        if orgid == 0:
            log.info("rest: Invalid OrgId")
            return False, None
        userid = data.get("userid", 'undef')
        if not is_valid_uuid(userid):
            log.info("rest: Invalid UserId")
            return False, None

        fileids = data.get("fids", [0])
        if len(fileids) == 0:
            fileids.append(0)
        fileids = '|'.join(map(str, fileids))

        oai_key = model_conf(const.OPEN_AI).get('api_key')
        oai_embeddings_model = "text-embedding-ada-002"
        oai_embeddings = OpenAIEmbeddings(openai_api_key=oai_key, model=oai_embeddings_model)
        myquery = oai_embeddings.embed_query(query)

        offset = data.get("offset", 0)
        rksize = data.get("size", 10)
        myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
        myredis.redis.select(0)
        pages = myredis.ft_search(embedded_query=myquery,
            vector_field="text_vector",
            hybrid_fields=myredis.create_hybrid_field2(orgid, fileids, 'internal', "category", "ka"),
            offset=offset,
            k=rksize)
        log.info(f"file-search: {len(pages)} matching pages.")
        if len(pages) == 0:
            return True, []

        results = []
        for i, page in enumerate(pages):
            filename = myredis.redis.hget(page.id, 'filename')
            pageurl = myredis.redis.hget(page.id, 'source')
            pagenum = myredis.redis.hget(page.id, 'page')
            pagetext = myredis.redis.hget(page.id, 'text')
            vscore = 1.0 - float(page.vector_score)
            if vscore < 0.75:
                break
            if pageurl is not None:
                filename = filename.decode()
                section = pagetext.decode()
                pageurl = pageurl.decode()
                if pagenum is not None:
                    pagenum = pagenum.decode()
                results.append({'filename':filename,'url':pageurl,'page':pagenum,'text':section,'score':vscore,'scale':75,'left':0,'top':120})
        if len(results) == 0:
            return True, []
        return True, results

    except Exception as e:
        log.info(e)
        return False, None
