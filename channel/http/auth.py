# encoding:utf-8

import jwt
import datetime
import time
import hashlib
import json
from flask import jsonify, request
from common import const
from common.redis import RedisSingleton
from config import channel_conf, common_conf_val
from common import log

def calculate_md5(string):
    md5_hash = hashlib.md5()
    md5_hash.update(string.encode('utf-8'))
    return md5_hash.hexdigest()

class Auth():
    def __init__(self, login):
    # argument 'privilegeRequired' is to set up your method's privilege
    # name
        self.login = login
        super(Auth, self).__init__()

    @staticmethod
    def encode_auth_token(username, password, login_time):
        """
        生成认证Token
        :param username: str
        :param password: str
        :param login_time: datetime
        :return: string
        """
        try:
            payload = {
                'iss': 'easiiosflow',  # 签名
                'exp': datetime.datetime.utcnow() + datetime.timedelta(days=0, hours=10),
                'iat': datetime.datetime.utcnow(),
                'data': {
                    'id': username,
                    'hashcode': calculate_md5(password.ljust(32, '#')),
                    'login_time': login_time
                }
            }
            return jwt.encode(
                payload,
                channel_conf(const.HTTP).get('http_auth_secret_key'),
                algorithm='HS256'
            )
        except Exception as e:
            return e

    @staticmethod
    def decode_auth_token(auth_token):
        """
        验证Token
        :param auth_token: str
        :return: json|str
        """
        try:
            # 取消过期时间验证
            options = {'verify_exp': False}
            payload = jwt.decode(auth_token, channel_conf(const.HTTP).get(
                'http_auth_secret_key'), algorithms='HS256', options=options)
            if ('data' in payload and 'id' in payload['data']):
                return payload
            else:
                raise jwt.InvalidTokenError
        except jwt.ExpiredSignatureError:
            return 'Token Expired'
        except jwt.InvalidTokenError:
            return 'Invalid Token'

def authenticate(username, password):
    """
    用户登录，登录成功返回token
    :param username: str
    :param password: str
    :return: str|boolean
    """
    myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
    credential = myredis.redis.hget('sfbot:'+username, 'password')
    if credential is None:
        return False
    credential = credential.decode()
    if (credential != password):
        return False
    login_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    token = Auth.encode_auth_token(username, password, login_time)
    return token

def identify(request):
    """
    用户鉴权
    :return: boolean,str
    """
    try:
        if (request is None):
            return False, None
        authorization = request.cookies.get('Authorization')
        if not authorization:
            authorization = request.headers.get('Authorization')
        if not authorization:
            log.info("auth:identify No Authorization")
            return False, None
        payload = Auth.decode_auth_token(authorization)
        if isinstance(payload, str):
            # jwt.ExpiredSignatureError 'Token Expired' 'Token已更改，请重新登录获取'
            # jwt.InvalidTokenError     'Invalid Token' '没有提供认证Token'
            result = payload
            log.info(f"auth:identify Authorization({authorization}): {result}")
            return False, None
        username = payload['data']['id']
        hashcode = payload['data']['hashcode']
        myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
        credential = myredis.redis.hget('sfbot:'+username, 'password')
        if credential is None:
            log.info("auth:identify No Credential")
            return False, None
        credential = credential.decode()
        rehash = calculate_md5(credential.ljust(32, '#'))
        log.info(f"auth:identify {username}: {hashcode}/{rehash}")
        if (rehash != hashcode):
            log.info("auth:identify Invalid Hashcode")
            return False, None
        return True, username

    except Exception as e:
        return False, None

def check_apikey(request):
    """
    KEY鉴权
    :return: boolean,str
    """
    try:
        if (request is None):
            return False, None
        sfkey = request.headers.get('X-SF-KEY')
        if not sfkey:
            log.info("auth:check_apikey No SfKey")
            return False, None
        keyval = common_conf_val('sflow_apikey', 'Sflow!')
        if (sfkey != keyval):
            log.info("auth:check_apikey Bad SfKey")
            return False, None
        data = json.loads(request.data)
        if not data:
            log.info("auth:check_apikey Parse Failed")
            return True, None
        mto = data['to']
        if not mto:
            log.info("auth:check_apikey No Recipient")
            return True, None
        routekey="sms:private:"+mto
        myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
        orgbot = myredis.redis.hget('sfbot:route', routekey)
        if (orgbot is None):
            log.info("auth:check_apikey No Bot-Route")
            return True, None
        return True, orgbot.decode()

    except Exception as e:
        log.info("auth:check_apikey "+str(e))
        return False, None
