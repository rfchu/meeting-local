import type { OutputLanguage } from "../types";

export interface RetrievalLanguageSupport {
  keyword_languages: string[];
  semantic_scope: "zh" | "model_dependent" | "unconfigured";
}

export function semanticLanguageHint(support?: RetrievalLanguageSupport) {
  if (support?.semantic_scope === "zh")
    return "当前内置向量模型面向中文。英文或跨语言语义检索可在运行管理中配置支持相应语言的本机 Embedding 模型；也可直接使用英文关键词。";
  if (support?.semantic_scope === "model_dependent")
    return "英文与跨语言语义检索能力取决于所选本机 Embedding 模型；连通测试不代表检索质量验证。";
  return "中英文关键词无需模型。语义检索需先配置本机 Embedding 模型，语言范围取决于模型。";
}

const key = "tingji.qa.output-language.v1";
type LanguageStorage = Pick<Storage, "getItem" | "setItem">;
function browserStorage() {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}
export function readAnswerLanguage(
  storage: LanguageStorage | undefined = browserStorage(),
): OutputLanguage {
  try {
    return storage?.getItem(key) === "en" ? "en" : "zh";
  } catch {
    return "zh";
  }
}
export function saveAnswerLanguage(
  language: OutputLanguage,
  storage: LanguageStorage | undefined = browserStorage(),
) {
  try {
    storage?.setItem(key, language);
  } catch {
    /* A blocked preference must not prevent asking a question. */
  }
}
