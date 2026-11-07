"""Shared Chinese/English keyword rules; never rewrite the cited source text."""
import re
import unicodedata

ENGLISH_STOPWORDS=frozenset('a an the and or of to in on at by for from with about what which who whose whom when where why how is are was were be been being do does did have has had can could would should it its this that these those we our you your they their please'.split())
CHINESE_STOPWORDS={'什么','哪些','是否','会议','是谁','我们','这个','怎么','如何','情况','一下'}


def normalize(text):
    return unicodedata.normalize('NFKC',text).casefold()


def tokens(text):
    return re.findall(r'[\u4e00-\u9fff]+|[^\W_]+(?:_[^\W_]+)*',normalize(text))


def question_query(question):
    terms=[]
    for term in tokens(question):
        if re.fullmatch(r'[\u4e00-\u9fff]+',term) and len(term)>4:
            terms.extend(term[i:i+n] for n in (3,2) for i in range(len(term)-n+1))
        else:terms.append(term)
    ignored=ENGLISH_STOPWORDS|CHINESE_STOPWORDS
    return ' '.join(list(dict.fromkeys(t for t in terms if t not in ignored))[:60])[:500]


def occurrences(normalized_text,term):
    term=normalize(term)
    if not term:return 0
    # English words/identifiers must not match inside unrelated words (art/quarter).
    # Chinese adjacency remains valid, e.g. "API接口" can be found with "API".
    start=r'(?<![a-z0-9_])' if re.match(r'[a-z0-9_]',term) else ''
    end=r'(?![a-z0-9_])' if re.search(r'[a-z0-9_]$',term) else ''
    return len(re.findall(start+re.escape(term)+end,normalized_text))
