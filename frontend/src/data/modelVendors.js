/**
 * 模型广场目录：厂商 + 旗下模型。
 *
 * ⚠️ 这里是**产品目录**，不是权威清单：
 *  - 各厂商模型迭代很快，`id` 可能随版本变化；
 *  - Base URL 与模型 ID 一律以厂商开放平台文档为准；
 *  - 界面会明确提示「以官方文档为准」，避免用户把示例值当成保证。
 *
 * 类型说明：
 *  chat 对话 / reasoning 推理 / multimodal 多模态 / text 文本生成
 *  embedding 向量 / code 代码 / image 图像 / video 视频 / speech 语音
 */

import doubaoLight from '../assets/vendors/doubao.png'
import doubaoDark from '../assets/vendors/doubao-dark.png'
import deepseekLight from '../assets/vendors/deepseek.png'
import deepseekDark from '../assets/vendors/deepseek-dark.png'
import qwenLight from '../assets/vendors/qwen.png'
import qwenDark from '../assets/vendors/qwen-dark.png'
import zhipuLight from '../assets/vendors/zhipu.png'
import zhipuDark from '../assets/vendors/zhipu-dark.png'
import kimiLight from '../assets/vendors/kimi.png'
import kimiDark from '../assets/vendors/kimi-dark.png'
import hunyuanLight from '../assets/vendors/hunyuan.png'
import hunyuanDark from '../assets/vendors/hunyuan-dark.png'
import minimaxLight from '../assets/vendors/minimax.png'
import minimaxDark from '../assets/vendors/minimax-dark.png'
import mimoLight from '../assets/vendors/mimo.png'
import mimoDark from '../assets/vendors/mimo-dark.png'
import openaiLight from '../assets/vendors/openai.png'
import openaiDark from '../assets/vendors/openai-dark.png'
import claudeLight from '../assets/vendors/claude.png'
import claudeDark from '../assets/vendors/claude-dark.png'
import geminiLight from '../assets/vendors/gemini.png'
import geminiDark from '../assets/vendors/gemini-dark.png'
import grokLight from '../assets/vendors/grok.png'
import grokDark from '../assets/vendors/grok-dark.png'

export const MODEL_TYPE_LABELS = {
  chat: '对话',
  reasoning: '推理',
  multimodal: '多模态',
  text: '文本生成',
  embedding: '向量',
  code: '代码',
  image: '图像',
  video: '视频',
  speech: '语音',
}

export function modelTypeLabel(type) {
  return MODEL_TYPE_LABELS[type] || type
}

//: 这些类型的模型不通过「自定义端点」接入，而是在创作页里直接使用。
//: 广场里点它们只应该跳转，不该建一个永远用不上的对话端点。
export const CREATIVE_TYPES = ['image', 'video', 'speech']

export function isCreativeModel(model) {
  return (model?.types || []).some((t) => CREATIVE_TYPES.includes(t))
}

export const MODEL_VENDORS = [
  {
    id: 'doubao',
    name: '豆包',
    en: 'Doubao · 火山方舟',
    icon: '豆',
    color: '#3B5BDB',
    desc: '字节跳动旗下，中文理解与多模态能力突出，企业级调用稳定。',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    note: '火山方舟部分场景要求填「推理接入点 ID」（ep-...），以控制台为准。',
    models: [
      { id: 'doubao-seed-1.6', name: '豆包 Seed 1.6', types: ['chat', 'multimodal'] },
      { id: 'doubao-1.5-pro-32k', name: '豆包 Pro 32K', types: ['chat', 'text'] },
      { id: 'doubao-1.5-lite-32k', name: '豆包 Lite 32K', types: ['chat', 'text'] },
      { id: 'doubao-1.5-vision-pro', name: '豆包视觉 Pro', types: ['multimodal'] },
      { id: 'doubao-embedding-large', name: '豆包向量 Large', types: ['embedding'] },
      { id: 'doubao-seedream-3-0-t2i', name: 'Seedream 3.0 文生图', types: ['image'] },
      { id: 'doubao-seedance-1-0-pro', name: 'Seedance 1.0 文生视频', types: ['video'] },
    ],
  },
  {
    id: 'qwen',
    name: '千问',
    en: 'Qwen · 阿里云百炼',
    icon: '千',
    color: '#615CED',
    desc: '阿里云通义千问系列，覆盖对话、推理、视觉、代码与向量。',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    note: '使用百炼的 OpenAI 兼容模式地址；模型 ID 以百炼控制台为准。',
    models: [
      { id: 'qwen-max', name: '通义千问 Max', types: ['chat', 'reasoning'] },
      { id: 'qwen-plus', name: '通义千问 Plus', types: ['chat'] },
      { id: 'qwen-turbo', name: '通义千问 Turbo', types: ['chat', 'text'] },
      { id: 'qwen3-235b-a22b', name: 'Qwen3 235B', types: ['reasoning', 'chat'] },
      { id: 'qwen-vl-max', name: 'Qwen-VL Max', types: ['multimodal'] },
      { id: 'qwen-coder-plus', name: 'Qwen Coder Plus', types: ['code', 'text'] },
      { id: 'text-embedding-v3', name: '文本向量 V3', types: ['embedding'] },
      { id: 'wanx2.1-t2i-turbo', name: '通义万相 2.1 Turbo', types: ['image'] },
      { id: 'wan2.2-t2v-plus', name: '通义万相 2.2 视频', types: ['video'] },
    ],
  },
  {
    id: 'deepseek',
    name: 'DeepSeek',
    en: 'DeepSeek',
    icon: 'DS',
    color: '#4C6EF5',
    desc: '深度求索，代码与数学推理见长，官方 API 原生兼容 OpenAI。',
    baseUrl: 'https://api.deepseek.com/v1',
    note: '推理模型（reasoner）不支持部分采样参数，调用时请按官方文档收敛。',
    models: [
      { id: 'deepseek-chat', name: 'DeepSeek Chat', types: ['chat', 'text'] },
      { id: 'deepseek-reasoner', name: 'DeepSeek Reasoner', types: ['reasoning'] },
    ],
  },
  {
    id: 'zhipu',
    name: '智谱',
    en: 'Zhipu · GLM',
    icon: '智',
    color: '#0B7285',
    desc: '智谱 AI GLM 系列，中文对话、推理与多模态能力均衡。',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    note: 'GLM-4.5 系列支持推理过程输出；向量模型与对话模型分开计费。',
    models: [
      { id: 'glm-4.5', name: 'GLM-4.5', types: ['chat', 'reasoning'] },
      { id: 'glm-4.5-air', name: 'GLM-4.5 Air', types: ['chat', 'reasoning'] },
      { id: 'glm-4-flash', name: 'GLM-4 Flash', types: ['chat', 'text'] },
      { id: 'cogview-3-flash', name: 'CogView 3 Flash', types: ['image'] },
      { id: 'cogview-4-250304', name: 'CogView 4', types: ['image'] },
      { id: 'cogvideox-3', name: 'CogVideoX-3', types: ['video'] },
      { id: 'glm-4v-flash', name: 'GLM-4V Flash', types: ['multimodal'] },
      { id: 'embedding-3', name: 'Embedding-3', types: ['embedding'] },
    ],
  },
  {
    id: 'kimi',
    name: 'Kimi',
    en: 'Moonshot AI',
    icon: 'K',
    color: '#1C7ED6',
    desc: '月之暗面，长上下文与中文写作见长，API 兼容 OpenAI。',
    baseUrl: 'https://api.moonshot.cn/v1',
    note: '长上下文模型按 token 计费，批量长文档场景建议先评估成本。',
    models: [
      { id: 'kimi-k2', name: 'Kimi K2', types: ['chat', 'reasoning'] },
      { id: 'moonshot-v1-128k', name: 'Moonshot 128K', types: ['chat', 'text'] },
      { id: 'moonshot-v1-32k', name: 'Moonshot 32K', types: ['chat', 'text'] },
      { id: 'moonshot-v1-8k', name: 'Moonshot 8K', types: ['chat', 'text'] },
    ],
  },
  {
    id: 'hunyuan',
    name: '腾讯混元',
    en: 'Hunyuan',
    icon: '混',
    color: '#1971C2',
    desc: '腾讯混元系列，覆盖通用对话、推理、视觉与向量。',
    baseUrl: 'https://api.hunyuan.cloud.tencent.com/v1',
    note: '使用混元的 OpenAI 兼容接口；密钥在腾讯云控制台创建。',
    models: [
      { id: 'hunyuan-turbos-latest', name: '混元 TurboS', types: ['chat'] },
      { id: 'hunyuan-pro', name: '混元 Pro', types: ['chat', 'reasoning'] },
      { id: 'hunyuan-vision', name: '混元 Vision', types: ['multimodal'] },
      { id: 'hunyuan-embedding', name: '混元向量', types: ['embedding'] },
    ],
  },
  {
    id: 'minimax',
    name: 'MiniMax',
    en: 'MiniMax',
    icon: 'M',
    color: '#E8590C',
    desc: 'MiniMax 系列，长文本与推理能力突出，提供 OpenAI 兼容接口。',
    baseUrl: 'https://api.minimax.chat/v1',
    note: '不同模型对温度、最大长度等参数限制不同，以官方文档为准。',
    models: [
      { id: 'MiniMax-M1', name: 'MiniMax M1', types: ['reasoning', 'chat'] },
      { id: 'MiniMax-Text-01', name: 'MiniMax Text 01', types: ['chat', 'text'] },
      { id: 'abab6.5s-chat', name: 'ABAB 6.5S', types: ['chat'] },
      { id: 'abab6.5g-chat', name: 'ABAB 6.5G', types: ['chat'] },
    ],
  },
  {
    id: 'mimo',
    name: '小米 MiMo',
    en: 'Xiaomi MiMo',
    icon: 'Mi',
    color: '#F08C00',
    desc: '小米开源模型系列，可通过 vLLM / Ollama 自建后接入。',
    baseUrl: '',
    note: '开源模型，无固定官方 API。请用 vLLM / Ollama 等以 OpenAI 兼容方式部署，再填写自建地址。',
    models: [
      { id: 'MiMo-7B', name: 'MiMo 7B', types: ['reasoning', 'chat'] },
      { id: 'MiMo-VL-7B', name: 'MiMo VL 7B', types: ['multimodal'] },
    ],
  },
  {
    id: 'openai',
    name: 'GPT',
    en: 'OpenAI',
    icon: 'GPT',
    color: '#212529',
    desc: 'OpenAI 系列，通用对话、多模态、推理与向量能力齐全。',
    baseUrl: 'https://api.openai.com/v1',
    note: 'o 系列推理模型对参数有额外限制；跨境访问可能受网络影响。',
    models: [
      { id: 'gpt-4o', name: 'GPT-4o', types: ['chat', 'multimodal'] },
      { id: 'gpt-4o-mini', name: 'GPT-4o mini', types: ['chat', 'text'] },
      { id: 'o3', name: 'o3', types: ['reasoning'] },
      { id: 'text-embedding-3-large', name: 'Embedding 3 Large', types: ['embedding'] },
      { id: 'gpt-image-1', name: 'GPT Image 1', types: ['image'] },
    ],
  },
  {
    id: 'claude',
    name: 'Claude',
    en: 'Anthropic',
    icon: 'C',
    color: '#D9480F',
    desc: 'Anthropic Claude 系列，长文本理解与复杂推理见长。',
    baseUrl: '',
    note: 'Claude 原生 Messages API 不是 OpenAI 规范。请填写 OneAPI / NewAPI 等兼容网关地址后再接入。',
    models: [
      { id: 'claude-sonnet-4-5', name: 'Claude Sonnet 4.5', types: ['chat', 'reasoning'] },
      { id: 'claude-opus-4-1', name: 'Claude Opus 4.1', types: ['reasoning'] },
      { id: 'claude-haiku-4-5', name: 'Claude Haiku 4.5', types: ['chat', 'text'] },
    ],
  },
  {
    id: 'gemini',
    name: 'Gemini',
    en: 'Google Gemini',
    icon: 'G',
    color: '#4285F4',
    desc: 'Google Gemini 系列，多模态与长上下文见长，官方提供 OpenAI 兼容端点。',
    // Google 的 OpenAI 兼容层：路径末尾的 / 不能省，否则会被拼成 .../openai/chat/completions
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    note: '在 Google AI Studio「Get API key」申请密钥；模型 ID 以官方文档为准。',
    models: [
      { id: 'gemini-2.5-pro', name: 'Gemini 2.5 Pro', types: ['reasoning', 'chat', 'multimodal'] },
      { id: 'gemini-2.5-flash', name: 'Gemini 2.5 Flash', types: ['chat', 'multimodal'] },
      { id: 'gemini-2.5-flash-lite', name: 'Gemini 2.5 Flash Lite', types: ['chat', 'text'] },
      { id: 'gemini-2.0-flash', name: 'Gemini 2.0 Flash', types: ['chat', 'multimodal'] },
    ],
  },
  {
    id: 'grok',
    name: 'Grok',
    en: 'xAI Grok',
    icon: 'X',
    color: '#111418',
    desc: 'xAI Grok 系列，联网与实时话题理解是它的长项。',
    baseUrl: 'https://api.x.ai/v1',
    note: '在 x.ai 控制台创建 API Key；模型 ID 以控制台列表为准。',
    models: [
      { id: 'grok-4', name: 'Grok 4', types: ['reasoning', 'chat'] },
      { id: 'grok-4-fast', name: 'Grok 4 Fast', types: ['chat'] },
      { id: 'grok-3', name: 'Grok 3', types: ['chat'] },
      { id: 'grok-3-mini', name: 'Grok 3 Mini', types: ['chat', 'text'] },
    ],
  },
]

/**
 * 厂商 Logo 资源（明/暗双版本）。
 *
 * 图标统一取自 Lobe Icons（`@lobehub/icons-static-png`，MIT），
 * 640×640 透明 PNG；Kimi 使用其单色版以适配浅色背景。
 * 小米 MiMo 在 Lobe 中只有字标，改用小米官方 mi 标识（见 docs/VENDOR_ICONS.md）。
 * Gemini / Grok 由 frontend/scripts/gen_vendor_logos.py 本地生成（几何化近似标识）。
 */
const LOGOS = {
  doubao: { light: doubaoLight, dark: doubaoDark },
  qwen: { light: qwenLight, dark: qwenDark },
  deepseek: { light: deepseekLight, dark: deepseekDark },
  zhipu: { light: zhipuLight, dark: zhipuDark },
  kimi: { light: kimiLight, dark: kimiDark },
  hunyuan: { light: hunyuanLight, dark: hunyuanDark },
  minimax: { light: minimaxLight, dark: minimaxDark },
  mimo: { light: mimoLight, dark: mimoDark },
  openai: { light: openaiLight, dark: openaiDark },
  claude: { light: claudeLight, dark: claudeDark },
  // Gemini / Grok：Lobe Icons 未收录这两家的可商用版本，
  // 这里用本地脚本生成的几何化近似标识（frontend/scripts/gen_vendor_logos.py）。
  gemini: { light: geminiLight, dark: geminiDark },
  grok: { light: grokLight, dark: grokDark },
}

// 运行期挂到目录对象上：避免在 10 个对象里各写一遍 logo 字段
for (const vendor of MODEL_VENDORS) {
  vendor.logo = LOGOS[vendor.id]
}

/** 从「我的接入」编辑一个不在目录里的自定义端点时使用的占位厂商。 */
export const CUSTOM_VENDOR = {
  id: 'custom',
  name: '自定义端点',
  en: 'Custom',
  icon: '＋',
  color: '#5F6E7A',
  desc: '用户自行接入的 OpenAI 兼容服务。',
  baseUrl: '',
  note: '',
  models: [],
}
