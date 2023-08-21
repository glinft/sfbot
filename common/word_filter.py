# encoding:utf-8

import ahocorasick
import time
from config import common_conf_val
from common.redis import RedisSingleton

def isnotad(char):
    return not (char.isalpha() or char.isdigit())

class WordFilter:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.words_data = {}
            cls._instance.sync_time = {}
        return cls._instance

    def load_words(self, orgid):
        current=time.time()
        orgkey="org:{}".format(orgid)
        syncat=self.sync_time.get(orgkey,0)
        if self.words_data.get(orgkey) is None or current-syncat>60:
            self.words_data[orgkey]={}
            wfkey=f"word:filter:{orgkey}"
            myredis = RedisSingleton(password=common_conf_val('redis_password', ''))
            myredis.redis.select(2)
            wfdata=myredis.redis.hgetall(wfkey)
            myredis.redis.select(0)
            for wfkey,wfval in wfdata.items():
                self.words_data[orgkey][wfkey.decode().lower()]=wfval.decode()
            self.sync_time[orgkey]=current
        return self.words_data[orgkey],self.sync_time[orgkey]

    def replace_sensitive_words(self, text, words_filt):
        if len(words_filt)==0:
            return text
        wfkeys = list(words_filt.keys())
        automaton = ahocorasick.Automaton()
        for word in wfkeys:
            automaton.add_word(word.lower(), (word,))
        automaton.make_automaton()
        matches = []
        for edidx, (word,) in automaton.iter(text.lower()):
            stidx = edidx - len(word) + 1
            if (stidx==0 or isnotad(text[stidx-1])) and (edidx==len(text)-1 or isnotad(text[edidx+1])):
                matches.append((stidx, edidx))
        if len(matches)==0:
            return text
        offset = 0
        replaced_text = list(text)
        for stidx, edidx in matches:
            filted_word = text[stidx:edidx+1]
            mapped_word = words_filt.get(filted_word.lower(), filted_word)
            replaced_text[offset+stidx:offset+edidx+1] = mapped_word
            offset += len(mapped_word)-len(filted_word)
        return ''.join(replaced_text)
