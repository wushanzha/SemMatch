import os
from typing import Dict
from semmatch.data import instance
from semmatch.utils.logger import logger
import collections
import tqdm
import simplejson as json
import re
DEFAULT_NON_PADDED_NAMESPACES = (".*tags", ".*labels")
DEFAULT_PADDING_TOKEN = "@@PADDING@@"
DEFAULT_OOV_TOKEN = "@@UNKNOWN@@"
NAMESPACE_PADDING_FILE = 'non_padded_namespaces.txt'
TOKEN_TO_INDEX_FILE = 'token_to_index.txt'
INDEX_TO_TOKEN_FILE = 'index_to_token.txt'


def namespace_match(patterns, name):
    for pattern in patterns:
        if re.search(pattern, name):
            return True
    return False


class ConditionDefaultDict(collections.defaultdict):
    def __init__(self, conditions, default_factory_1, default_factory_2):
        super().__init__()
        self._conditions = conditions
        self._default_factory_1 = default_factory_1
        self._default_factory_2 = default_factory_2

    def __missing__(self, key):
        if namespace_match(self._conditions, key):
            value = self._default_factory_1()
        else:
            value = self._default_factory_2()
        collections.defaultdict.__setitem__(self, key, value)
        return value


class TokenToIndexDict(ConditionDefaultDict):
    def __init__(self, non_padded_namespaces, padding_token,  oov_token):
        super().__init__(non_padded_namespaces, lambda: {}, lambda: {padding_token: 0, oov_token: 1})


class IndexToTokenDict(ConditionDefaultDict):
    def __init__(self, non_padded_namespaces, padding_token,  oov_token):
        super().__init__(non_padded_namespaces, lambda: {}, lambda: {0: padding_token, 1: oov_token})


class Vocabulary(object):
    def __init__(self, counter=None, non_padded_namespaces=DEFAULT_NON_PADDED_NAMESPACES):
        self._padding_token = DEFAULT_PADDING_TOKEN
        self._oov_token = DEFAULT_OOV_TOKEN
        self._non_padded_namespaces = set(non_padded_namespaces)
        self._token_to_index = TokenToIndexDict(self._non_padded_namespaces, self._padding_token, self._oov_token)
        self._index_to_token = IndexToTokenDict(self._non_padded_namespaces, self._padding_token, self._oov_token)
        self._extend(counter)

    def save_to_files(self, directory):
        os.makedirs(directory, exist_ok=True)
        if os.listdir(directory):
            logger.warning("Vocabulary directory %s is not empty", directory)
        with open(os.path.join(directory, NAMESPACE_PADDING_FILE), 'w', encoding='utf-8') as json_file:
            json.dump(list(self._non_padded_namespaces), json_file)

        with open(os.path.join(directory, TOKEN_TO_INDEX_FILE), "w", encoding='utf-8') as json_file:
            json.dump(self._token_to_index, json_file)

        with open(os.path.join(directory, INDEX_TO_TOKEN_FILE), 'w', encoding='utf-8') as json_file:
            json.dump(self._index_to_token, json_file)

    def load_from_files(self, directory):
        if not os.path.exists(directory):
            logger.error("Vocabulary directory %s does not exist.", directory)
            return False
        namespaces_file = os.path.join(directory, NAMESPACE_PADDING_FILE)
        if not os.path.exists(namespaces_file):
            logger.error("Vocabulary namespaces file %s does not exist", namespaces_file)
            return False
        token_to_index_file = os.path.join(directory, TOKEN_TO_INDEX_FILE)
        if not os.path.exists(token_to_index_file):
            logger.error("Vocabulary token to index file %s does not exist", token_to_index_file)
            return False
        index_to_token_file = os.path.join(directory, INDEX_TO_TOKEN_FILE)
        if not os.path.exists(index_to_token_file):
            logger.error("Vocabulary index to token file %s does not exist", index_to_token_file)
            return False
        with open(namespaces_file, 'r', encoding='utf-8') as json_file:
            self._non_padded_namespaces = set(json.load(json_file))

        with open(token_to_index_file, 'r', encoding='utf-8') as json_file:
            self._token_to_index = json.load(json_file)

        with open(index_to_token_file, 'r', encoding='utf-8') as json_file:
            self._index_to_token = json.load(json_file)
            for (namespace, mapping) in self._index_to_token.items():
                new_mapping = dict()
                for (index, token) in mapping.items():
                    new_mapping[int(index)] = token
                self._index_to_token[namespace] = new_mapping

        if self.valid():
            return True
        else:
            return False

    def valid(self):
        for namespace, mapping in self._index_to_token.items():
            for (index, token) in mapping.items():
                if self._token_to_index[namespace][token] != int(index):
                    logger.error("index/token in index_to_token : %s/%s not in token_to_index"%(index, token))
                    return False
        return True

    def get_token_index(self, token, namespace):
        token = str(token)
        if token in self._token_to_index[namespace]:
            return self._token_to_index[namespace][token]
        else:
            try:
                return self._token_to_index[namespace][self._oov_token]
            except KeyError:
                logger.error('Namespace: %s', namespace)
                logger.error('Token: %s', token)
                raise

    def add_token_to_vocab(self, token, namespace):
        if token not in self._token_to_index[namespace]:
            index = len(self._token_to_index[namespace].keys())
            token = str(token)
            self._token_to_index[namespace][token] = index
            self._index_to_token[namespace][index] = token
            return index
        else:
            return self._token_to_index[namespace][token]

    def _extend(self, counter=None):
        counter = counter or {}
        for namespace in counter:
            for token in counter[namespace].keys():
                self.add_token_to_vocab(token, namespace)

    @classmethod
    def init_from_instances(cls, instances):
        logger.info("create vocab from instance")
        namespace_counter = collections.defaultdict(lambda: collections.defaultdict(int))
        example_features = None
        for i, instance in enumerate(tqdm.tqdm(instances)):
            instance.count_vocab(namespace_counter)
        return cls(namespace_counter)
