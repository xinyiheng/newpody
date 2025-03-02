import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
import time
import json
import asyncio
import os
from typing import List, Dict, Literal, Annotated
import shutil
import httpx
import ormsgpack
from pydantic import BaseModel, conint
import aiohttp
import random

class ServeTTSRequest(BaseModel):
    text: str
    reference_id: str = "57eab548c7ed4ddc974c4c153cb015b2"
    chunk_length: Annotated[int, conint(ge=100, le=300, strict=True)] = 200
    format: Literal["wav", "pcm", "mp3"] = "mp3"
    mp3_bitrate: Literal[64, 128, 192] = 192
    normalize: bool = True
    latency: Literal["normal", "balanced"] = "normal"

class PodcastGenerator:
    def __init__(self):
        self.rss_url = "https://www.inoreader.com/stream/user/1005507650/tag/%E5%9B%BD%E5%86%85%E5%87%BA%E7%89%88%E5%95%86%E5%85%AC%E4%BC%97%E5%8F%B7"
        # 从环境变量获取 API key
        self.api_key = os.environ.get('API_KEY')
        if not self.api_key:
            raise ValueError("API_KEY environment variable is not set")
        self.api_base = "https://openrouter.ai/api/v1/chat/completions"
        
        # 所有路径都相对于 main 目录
        self.cache_file = "article_cache.json"
        self.web_dir = "web"
        self.public_dir = os.path.join(self.web_dir, "public")
        self.podcasts_dir = os.path.join(self.public_dir, "podcasts")
        self.index_file = os.path.join(self.public_dir, "podcast_index.json")
        self.fish_api_key = os.environ.get('FISH_API_KEY')
        if not self.fish_api_key:
            raise ValueError("FISH_API_KEY environment variable is not set")
        
        # 确保必要的目录存在
        for directory in [self.web_dir, self.public_dir, self.podcasts_dir]:
            if not os.path.exists(directory):
                os.makedirs(directory)

        # 添加 headers 配置
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/rss+xml,application/xml;q=0.9,*/*;q=0.8'
        }

    def load_cache(self) -> Dict:
        """加载文章缓存，并清理过期内容"""
        try:
            print(f"\n尝试加载缓存文件: {self.cache_file}")
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                    
                # 打印更详细的缓存信息
                print(f"缓存文件大小: {os.path.getsize(self.cache_file)} 字节")
                print(f"缓存条目数量: {len(cache.get('articles', {}))}")
                print("缓存的文章标题:")
                for url, article in cache.get('articles', {}).items():
                    print(f"- {article.get('data', {}).get('title', 'No title')}")
                
                # 清理7天前的缓存
                current_time = datetime.now()
                cleaned_cache = {'articles': {}}
                
                for url, article_data in cache['articles'].items():
                    try:
                        article_time = datetime.strptime(article_data['timestamp'], '%Y-%m-%d %H:%M:%S')
                        if (current_time - article_time).days < 7:
                            cleaned_cache['articles'][url] = article_data
                    except Exception as e:
                        print(f"清理缓存条目时出错: {e}")
                        continue
                
                print(f"已加载缓存，包含 {len(cleaned_cache['articles'])} 个有效条目")
                return cleaned_cache
            
            print("缓存文件不存在，创建新缓存")
            return {'articles': {}}
        except Exception as e:
            print(f"加载缓存失败: {e}")
            return {'articles': {}}

    def save_cache(self, cache: Dict):
        """保存文章缓存"""
        try:
            print(f"当前工作目录: {os.getcwd()}")
            print(f"缓存文件路径: {os.path.abspath(self.cache_file)}")
            print(f"\n正在保存缓存，共 {len(cache['articles'])} 个条目")
            
            # 确保缓存目录存在
            cache_dir = os.path.dirname(os.path.abspath(self.cache_file))
            if not os.path.exists(cache_dir):
                os.makedirs(cache_dir)
                print(f"创建缓存目录: {cache_dir}")
            
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            
            # 验证文件是否成功写入
            if os.path.exists(self.cache_file):
                file_size = os.path.getsize(self.cache_file)
                print(f"缓存保存成功: {file_size} 字节")
            else:
                print("警告：缓存文件未成功创建")
            
        except Exception as e:
            print(f"保存缓存失败: {e}")
            import traceback
            print(traceback.format_exc())

    def update_podcast_index(self, podcast_data):
        """更新播客索引文件"""
        try:
            print(f"\n正在更新索引文件: {self.index_file}")
            
            # 从 gh-pages 分支获取现有索引
            index_url = f"https://raw.githubusercontent.com/xinyiheng/newpody/gh-pages/podcast_index.json"
            try:
                response = requests.get(index_url)
                if response.status_code == 200:
                    index = response.json()
                    print(f"成功从 gh-pages 获取现有索引，包含 {len(index['podcasts'])} 个播客")
                else:
                    print("无法获取现有索引，创建新索引")
                    index = {'podcasts': []}
            except Exception as e:
                print(f"获取现有索引失败: {e}")
                index = {'podcasts': []}

            # 构建新的播客数据
            new_podcast = {
                'id': podcast_data['id'],
                'date': podcast_data['date'],
                'title': podcast_data['title'],
                'transcript_path': podcast_data['transcript_path'],  # 使用传入的路径，不再硬编码
                'audio_path': podcast_data.get('audio_path'),  # 使用传入的路径
                'highlight': podcast_data.get('highlight', "探索出版行业的最新动态，聆听行业专家的深度解析")  # 添加副标题，如果没有则使用默认值
            }
            
            # 打印调试信息
            print(f"新播客数据: {new_podcast}")
            
            # 添加新播客信息到列表开头
            index['podcasts'].insert(0, new_podcast)
            
            # 只保留最近50期
            index['podcasts'] = index['podcasts'][:50]
            
            # 保存更新后的索引
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
            
            print(f"索引文件已更新，现在包含 {len(index['podcasts'])} 个播客")
                
        except Exception as e:
            print(f"更新索引文件失败: {e}")
            import traceback
            print(traceback.format_exc())

    async def generate_audio(self, text: str, timestamp: str) -> str:
        """使用 Fish Audio TTS 生成音频"""
        print("开始生成音频...")
        try:
            podcast_dir = os.path.join(self.podcasts_dir, timestamp)
            if not os.path.exists(podcast_dir):
                os.makedirs(podcast_dir)
            
            request = ServeTTSRequest(
                text=text,
                reference_id="74a543044a7b445696f6fc77a8aafa8d",
                mp3_bitrate=192,
                normalize=True,
                latency="normal"
            )

            audio_file = os.path.join(podcast_dir, 'podcast.mp3')
            
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    "https://api.fish.audio/v1/tts",
                    content=ormsgpack.packb(request, option=ormsgpack.OPT_SERIALIZE_PYDANTIC),
                    headers={
                        "authorization": f"Bearer {self.fish_api_key}",
                        "content-type": "application/msgpack",
                    },
                    timeout=None,
                ) as response:
                    with open(audio_file, 'wb') as f:
                        async for chunk in response.aiter_bytes():
                            f.write(chunk)
            
            print(f"✅ 音频文件已保存到: {audio_file}")
            return audio_file
        except Exception as e:
            print(f"生成音频失败: {e}")
            return None

    def fetch_article_content(self, url, max_retries=3):
        """获取文章内容"""
        print(f"\n正在处理URL: {url}")
        
        for attempt in range(max_retries):
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8'
                }
                
                response = requests.get(url, headers=headers, timeout=30)
                response.encoding = 'utf-8'
                
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # 针对微信文章的内容提取
                if 'mp.weixin.qq.com' in url:
                    article = soup.find('div', id='js_content')
                    if article:
                        # 清理文章内容
                        for tag in article.find_all(['script', 'style', 'iframe', 'img']):
                            tag.decompose()
                        
                        # 获取所有文本段落
                        paragraphs = []
                        for p in article.find_all(['p', 'section']):
                            text = p.get_text().strip()
                            # 过滤无效内容
                            if (text and 
                                len(text) > 10 and  # 忽略太短的段落
                                not any(x in text for x in [
                                    '微信', '扫描', '二维码', '关注我们', '点击', '阅读原文',
                                    '长按识别', '复制链接', '网购', '电商', '加入会员'
                                ])):
                                paragraphs.append(text)
                        
                        content = '\n\n'.join(paragraphs)
                        
                        if len(content) > 500:
                            print(f"成功获取文章内容，长度: {len(content)} 字符")
                            return content
                        else:
                            print(f"文章内容太短，跳过 (长度: {len(content)} 字符)")
                            return None
                else:
                    # 处理其他网站的文章
                    content_selectors = [
                        'article', 
                        '.article-content',
                        '.post-content',
                        '.content',
                        '.article',
                        '.rich_media_content'
                    ]
                    
                    for selector in content_selectors:
                        content_element = soup.select_one(selector)
                        if content_element:
                            # 清理内容
                            for tag in content_element.find_all(['script', 'style', 'iframe', 'img']):
                                tag.decompose()
                            
                            paragraphs = []
                            for p in content_element.find_all(['p', 'section']):
                                text = p.get_text().strip()
                                if text and len(text) > 10:
                                    paragraphs.append(text)
                            
                            content = '\n\n'.join(paragraphs)
                            if len(content) > 500:
                                print(f"成功获取文章内容，长度: {len(content)} 字符")
                                return content
                            else:
                                print(f"文章内容太短，跳过")
                                return None
                
                if attempt < max_retries - 1:
                    print(f"未找到文章内容，尝试重新获取 (尝试 {attempt + 2}/{max_retries})")
                    time.sleep(3)
                    continue
                else:
                    print("多次尝试后仍未获取到有效内容")
                    return None
                
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"获取失败，尝试重新获取 (尝试 {attempt + 2}/{max_retries}): {e}")
                    time.sleep(3)
                    continue
                else:
                    print(f"多次尝试后获取失败: {e}")
                    return None
        
        return None

    def should_skip_article(self, title: str, content: str) -> tuple[bool, str]:
        """检查文章是否应该跳过
        Returns:
            tuple: (是否跳过, 跳过原因)
        """
        # 检查文章长度
        if len(content) < 500:
            return True, 'too_short'
        
        # 检查标题关键词
        skip_keywords = ['招聘', '会议', '党委', '表彰', '招募']
        for keyword in skip_keywords:
            if keyword in title:
                return True, f'标题包含关键词: {keyword}'
            
        # 检查内容开头关键词
        content_start = content[:100]  # 只检查开头100个字符
        start_keywords = ['招募', '诚聘', '报名']
        for keyword in start_keywords:
            if keyword in content_start:
                return True, f'内容开头包含关键词: {keyword}'
            
        return False, ''

    def save_article_to_cache(self, article: Dict, cache: Dict, filter_reason: str = None):
        """保存文章到缓存"""
        url = article['link']
        cache['articles'][url] = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data': {
                'title': article.get('title', ''),
                'author': article.get('author', '未知作者'),
                'source': article.get('source', ''),
                'link': url,
                'pub_time': article.get('pub_time', ''),
            }
        }
        
        # 如果有过滤原因,添加标记
        if filter_reason:
            cache['articles'][url]['filter_reason'] = filter_reason

    def fetch_rss_articles(self, num_pages=5):
        """获取RSS文章列表，支持多页获取和去重"""
        try:
            print("开始获取RSS文章...")
            
            articles = []
            cache = self.load_cache()
            seen_urls = set()  # 用于跟踪本次已处理的URL
            
            # 获取已处理的URL和它们的时间戳
            processed_urls = {}
            for url, data in cache['articles'].items():
                try:
                    timestamp = datetime.strptime(data['timestamp'], '%Y-%m-%d %H:%M:%S')
                    processed_urls[url] = timestamp
                except:
                    continue
                    
            # 修改分页逻辑
            page_urls = [
                f"{self.rss_url}&n=100",  # 直接获取100篇
                f"{self.rss_url}?n=100",  # 备用格式
            ]
            
            for page_url in page_urls:
                print(f"\n尝试获取文章 (URL: {page_url})...")
                
                try:
                    # 使用类的 headers 属性
                    response = requests.get(page_url, headers=self.headers, timeout=30)
                    feed = feedparser.parse(response.text)
                    
                    if not feed.entries:
                        print(f"此URL没有返回文章，尝试下一个URL")
                        continue
                        
                    print(f"找到 {len(feed.entries)} 篇文章")
                    
                    for entry in feed.entries:
                        # 跳过已经处理过的URL
                        if entry.link in seen_urls:
                            continue
                            
                        if entry.link in processed_urls:
                            cache_time = processed_urls[entry.link]
                            if (datetime.now() - cache_time).days < 7:
                                print(f"跳过最近处理的文章: {entry.get('title', 'No title')}")
                                continue
                        
                        seen_urls.add(entry.link)
                        
                        print(f"\n处理文章: {entry.get('title', 'No title')}")
                        
                        # 构建基本文章信息
                        article = {
                            'title': entry.title,
                            'author': entry.get('dc_creator', '未知作者'),
                            'source': entry.get('source', {}).get('title', '未知来源'),
                            'link': entry.link,
                            'pub_time': entry.get('published', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                        }
                        
                        # 获取文章内容
                        content = self.fetch_article_content(entry.link)
                        if content is None:
                            print(f"获取文章内容失败")
                            self.save_article_to_cache(article, cache, 'fetch_failed')
                            continue
                            
                        article['content'] = content
                        
                        # 检查是否应该跳过
                        should_skip, reason = self.should_skip_article(article['title'], content)
                        if should_skip:
                            print(f"跳过文章，原因: {reason}")
                            self.save_article_to_cache(article, cache, reason)
                            continue
                            
                        articles.append(article)
                        self.save_article_to_cache(article, cache)
                        print(f"成功添加文章: {entry.title}")
                        
                        if len(articles) >= 100:  # 限制最大文章数
                            print("\n已达到最大文章数限制(100)")
                            break
                        
                        time.sleep(2)  # 避免频繁请求
                    
                    if len(articles) >= 100:
                        break
                        
                except Exception as e:
                    print(f"尝试URL失败: {e}")
                    continue
                
            # 保存更新后的缓存
            self.save_cache(cache)
            
            print(f"\n成功获取 {len(articles)} 篇新文章")
            return articles
            
        except Exception as e:
            print(f"获取RSS文章失败: {e}")
            return []

    async def summarize_single_article(self, article: Dict) -> Dict:
        """异步总结单篇文章，带重试机制"""
        max_retries = 3
        retry_delay = 5  # 秒
        
        for attempt in range(max_retries):
            try:
                prompt = f"""请将这篇文章总结为有价值的内容，让读者能学到具体的知识。

要求：
1. 开头简要介绍文章核心话题，不要泛泛而谈，直击重点
2. 列出文章最重要的2-3个具体事实或数据，不要
用"进行了深入探讨"这样的空话
3. 使用中文数字标记大标题（一、二、三），使用阿拉伯数字标记具体内容（1. 2. 3.）
4. 如果文章讨论了历史，请具体说明是什么时间、什么事件、产生了什么影响
5. 如果文章介绍了新事物，请说明它的独特之处和实际应用场景
6. 如果文章有争议观点，请说明各方论据
7. 总结文章最有价值的发现或启示，以及对读者最有帮助的建议
8. 语言要生动具体，避免空泛的形容词
8. 按照"背景介绍 - 关键发现 - 实际意义"的结构组织内容
10. 每部分控制在200-300字，确保简明扼要但包含必要细节
11. 段落之间保持一个空行，使用中文数字作为标题

文章标题：{article['title']}
作者：{article['author']}
内容：{article['content']}"""

                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.api_base,
                        headers=headers,
                        json={
                            "model": "google/gemini-2.0-flash-001",
                            "messages": [{"role": "user", "content": prompt}]
                        },
                        timeout=30
                    ) as response:
                        result = await response.json()
                        
                        # 处理速率限制错误
                        if 'error' in result and result['error'].get('code') == 429:
                            if attempt < max_retries - 1:
                                wait_time = retry_delay * (attempt + 1)
                                print(f"遇到速率限制，等待 {wait_time} 秒后重试...")
                                await asyncio.sleep(wait_time)
                                continue
                            else:
                                print("达到最大重试次数，跳过此文章")
                                return None
                        
                        # 处理正常响应
                        if 'choices' in result:
                            summary = result["choices"][0]["message"]["content"].strip()
                        elif 'response' in result:
                            summary = result["response"].strip()
                        else:
                            print(f"API响应格式异常: {result}")
                            return None
                        
                        return {
                            'title': article['title'],
                            'summary': summary,
                            'source': article.get('source', '未知来源'),
                            'pub_time': article.get('pub_time', ''),
                            'link': article.get('link', ''),
                            'content': article.get('content', '')
                        }
                        
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"总结文章失败: {article['title']}, 错误: {e}, 将重试...")
                    await asyncio.sleep(retry_delay)
                    continue
                else:
                    print(f"总结文章最终失败: {article['title']}, 错误: {e}")
                    return None

    async def summarize_articles(self, articles: List[Dict]) -> List[Dict]:
        """并行总结多篇文章，处理速率限制"""
        print(f"\n开始总结 {len(articles)} 篇文章...")
        
        # 每批处理的文章数
        batch_size = 15  # 考虑到免费版每分钟20次的限制
        summaries = []
        
        for i in range(0, len(articles), batch_size):
            batch = articles[i:i + batch_size]
            print(f"\n处理第 {i+1} 到 {i+len(batch)} 篇文章...")
            
            tasks = [self.summarize_single_article(article) for article in batch]
            batch_summaries = await asyncio.gather(*tasks)
            
            # 过滤掉失败的总结
            valid_summaries = [s for s in batch_summaries if s is not None]
            summaries.extend(valid_summaries)
            
            # 如果不是最后一批，等待一分钟
            if i + batch_size < len(articles):
                print("等待60秒以避免速率限制...")
                await asyncio.sleep(60)
        
        print(f"完成 {len(summaries)} 篇文章的总结")
        return summaries

    def clear_cache_entry(self, url):
        """删除缓存中的特定文章记录"""
        try:
            cache = self.load_cache()
            if url in cache['articles']:
                del cache['articles'][url]
                self.save_cache(cache)
                print(f"已删除缓存记录: {url}")
            else:
                print(f"未找到缓存记录: {url}")
        except Exception as e:
            print(f"删除缓存记录失败: {e}")

    def format_datetime(self, datetime_str: str) -> str:
        """将各种格式的时间转换为统一的中文格式"""
        try:
            # 处理带时区的格式 (e.g. "Thu, 27 Feb 2025 16:03:21 +0000")
            dt = datetime.strptime(datetime_str, '%a, %d %b %Y %H:%M:%S %z')
            # 转换为北京时间 (UTC+8)
            dt = dt.astimezone(timezone(timedelta(hours=8)))
        except:
            try:
                # 处理不带时区的格式
                dt = datetime.strptime(datetime_str, '%Y-%m-%d %H:%M:%S')
            except:
                # 如果解析失败，返回当前时间
                dt = datetime.now()
        
        return dt.strftime('%Y年%m月%d日 %H:%M')

    async def generate_final_summary(self, summaries: List[Dict], timestamp: str) -> str:
        """生成最终的汇总摘要和播报稿"""
        try:
            podcast_dir = os.path.join(self.podcasts_dir, timestamp)
            
            # 保存总结到正确位置
            summary_file = os.path.join(podcast_dir, 'summary.txt')
            summary_html = os.path.join(podcast_dir, 'summary.html')  # 新增HTML文件
            articles_file = os.path.join(podcast_dir, 'articles.txt')
            script_file = os.path.join(podcast_dir, 'script.txt')
            
            # 保存总结和原文
            with open(summary_file, 'w', encoding='utf-8') as f_summary, \
                 open(articles_file, 'w', encoding='utf-8') as f_articles, \
                 open(summary_html, 'w', encoding='utf-8') as f_html:  # 新增HTML文件写入
                
                # 添加文章数量信息
                f_summary.write("出版行业新闻总结\n\n")
                f_summary.write(f"今天总结了 {len(summaries)} 篇文章\n\n")
                f_summary.write("="*50 + "\n\n")  # 添加分隔线
                
                f_articles.write("出版行业新闻原文\n\n")
                
                # 开始写入HTML文件头部
                f_html.write("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>出版行业新闻总结</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; background-color: #f8f5e6;">
    <div style="text-align: center; margin-bottom: 30px; padding-bottom: 20px; border-bottom: 2px solid #ddd;">
        <h1 style="color: #2c3e50; text-align: center; margin-bottom: 20px;">出版行业新闻总结</h1>
        <p>今天总结了 """ + str(len(summaries)) + """ 篇文章</p>
    </div>
""")
                
                # 收集所有文章标题和关键词
                all_titles = []
                all_keywords = []
                
                for i, s in enumerate(summaries, 1):
                    formatted_time = self.format_datetime(s['pub_time'])
                    
                    # 收集标题
                    all_titles.append(s['title'])
                    
                    # 提取关键词 (简单分词)
                    words = s['title'].split()
                    for word in words:
                        if len(word) > 1 and word not in ['的', '了', '和', '与', '在', '是', '有', '为']:
                            all_keywords.append(word)
                    
                    # 写入总结
                    f_summary.write(f"文章{i}/{len(summaries)}\n")  # 显示当前是第几篇，共几篇
                    f_summary.write(f"标题：{s['title']}\n")
                    f_summary.write(f"来源：{s['source']}\n")
                    f_summary.write(f"原文链接：{s['link']}\n")
                    f_summary.write(f"发布时间：{formatted_time}\n")
                    f_summary.write(f"总结：\n{s['summary']}\n")
                    f_summary.write("\n" + "="*50 + "\n\n")
                    
                    # 写入原文
                    f_articles.write(f"文章{i}\n")
                    f_articles.write(f"标题：{s['title']}\n")
                    f_articles.write(f"来源：{s['source']}\n")
                    f_articles.write(f"原文链接：{s['link']}\n")
                    f_articles.write(f"发布时间：{formatted_time}\n")
                    f_articles.write(f"原文：\n{s.get('content', '未获取到原文')}\n")
                    f_articles.write("\n" + "="*50 + "\n\n")
                    
                    # 写入HTML格式
                    f_html.write(f"""
    <div style="margin-bottom: 30px; padding: 25px; border-radius: 10px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); background-color: #fff; transition: transform 0.2s ease;">
        <div style="font-size: 1.4em; font-weight: bold; margin-bottom: 10px; color: #2c3e50; border-bottom: 2px solid #eee; padding-bottom: 10px;">文章{i}/{len(summaries)}: {s['title']}</div>
        <div style="color: #666; margin-bottom: 15px; font-size: 0.9em;">
            <div style="padding: 3px 0;"><strong>来源：</strong>{s['source']}</div>
            <div style="padding: 3px 0;"><strong>原文链接：</strong><a href="{s['link']}" target="_blank" style="color: #3498db; text-decoration: none; font-weight: 500;">{s['link']}</a></div>
            <div style="padding: 3px 0;"><strong>发布时间：</strong>{formatted_time}</div>
        </div>
        <div style="margin-top: 15px; line-height: 1.7; text-align: justify;">
            <strong>总结：</strong><br>
            {s['summary'].replace('\n', '<br>').replace('*', '').replace('#', '')}
        </div>
    </div>
""")
                
                # 写入HTML文件尾部
                f_html.write("""
    <div style="text-align: center; margin-top: 40px; padding-top: 20px; border-top: 2px solid #ddd; color: #666; font-size: 0.9em;">
        <p>© 出版电台 - 每日为您提供出版行业最新资讯</p>
    </div>
</body>
</html>
""")
            
            # 生成播报稿
            article_count = len(summaries)
            input_text = "\n\n".join([
                f"文章{i+1}:\n标题: {s['title']}\n来源: {s['source']}\n总结:\n{s['summary']}"
                for i, s in enumerate(summaries)
            ])
            
            prompt = f"""你是出版电台的主播，需要将以下{article_count}篇文章整理成适合朗读的播报内容。

内容材料：
{input_text}

要求：
1. 开场语固定为："各位听众，这里是出版电台。今天我们为您带来出版行业的最新资讯。点击音频左下角的"查看文稿"，您可以获取本播报涉及的所有文章原文以及内容总结。
2. 每篇文章的播报需包含：
   - 以自然、亲切的方式介绍文章标题和来源（如"今天我们先来看一篇来自XX的文章，标题是……"）
   - 核心观点和关键信息（200-300字），语气生动，突出有趣细节
   - 销量或营销亮点（如果内容中有），用引导性语言呈现（如"值得一提的是……"）
3. 文章之间使用自然过渡语连接（如"接下来"、"另外"、"让我们转向"），保持流畅
4. 使用播音腔语气，正式但不呆板，适当加入提问或引导（如"你知道吗？"、"这意味着什么呢？"）以吸引听众
5. 通过语气和停顿来控制节奏，不要在文本中加入任何控制词（如"稍停"、"停顿"等）
6. 结尾固定为："感谢收听出版电台，我们下期再见。"
7. 不要使用等*、#、--等不能朗读的符号，确保文本适合直接朗读
8. 必须处理所有提供的文章
9. 结尾要加上"如果您喜欢本节目，请点击分享。"

请直接输出播报内容。
"""

            # 生成自然的广播式副标题
            # 选择3-5个不重复的关键词
            selected_keywords = []
            random.shuffle(all_keywords)
            for keyword in all_keywords:
                if keyword not in selected_keywords:
                    selected_keywords.append(keyword)
                    if len(selected_keywords) >= 5:
                        break
            
            # 如果关键词不足，使用标题中的词补充
            if len(selected_keywords) < 3:
                for title in all_titles:
                    words = title.split()
                    for word in words:
                        if word not in selected_keywords and len(word) > 1:
                            selected_keywords.append(word)
                            if len(selected_keywords) >= 5:
                                break
                    if len(selected_keywords) >= 5:
                        break
            
            # 生成广播式副标题
            if len(selected_keywords) >= 3:
                highlight = f"大家好，这里是出版电台每日播报，今天出版圈的最新动态，从{selected_keywords[0]}，到{selected_keywords[1]}，"
                
                if len(selected_keywords) >= 5:
                    highlight += f"再到{selected_keywords[2]}，以及{selected_keywords[3]}，当然也少不了{selected_keywords[4]}。"
                elif len(selected_keywords) >= 4:
                    highlight += f"再到{selected_keywords[2]}，当然也少不了{selected_keywords[3]}。"
                else:
                    highlight += f"当然也少不了{selected_keywords[2]}。"
            else:
                # 如果无法生成自然的广播副标题，从预设的标题中随机选择一个
                preset_highlights = [
                    "牛人猛读书单，你读过几本？",
                    "重磅新书来袭！解锁职场成功密码",
                    "数字化浪潮下的出版业新机遇",
                    "畅销书背后的营销秘密大揭秘！",
                    "从经典到畅销，好书如何炼成？",
                    "编辑推荐：本月必读好书清单",
                    "阅读改变命运，这本书让你脱胎换骨",
                    "出版界年度盛事，不容错过的重磅新书",
                    "AI时代的阅读指南，提升认知就靠它",
                    "解锁创作密码，成为下一个畅销书作家",
                    "出版人必看！行业趋势与未来方向",
                    "读者口碑爆棚，这些好书不容错过",
                    "数字出版新玩法，传统出版社如何转型？",
                    "畅销书作家的秘密武器，原来是这个！",
                    "出版业最新动态，这些变化你知道吗？",
                    "好内容如何变现？出版人的营销指南",
                    "读书人的福利来了！这些新书值得关注",
                    "出版圈大事件，行业巨头最新动向",
                    "新媒体时代的出版策略，专家这样说",
                    "必读书单出炉，你跟上了吗？"
                ]
                highlight = random.choice(preset_highlights)

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            # 生成播报稿
            async with aiohttp.ClientSession() as session:
                broadcast_response = await session.post(
                    self.api_base,
                    headers=headers,
                    json={
                        "model": "google/gemini-2.0-flash-001",
                        "messages": [{"role": "user", "content": prompt}]
                    },
                    timeout=180
                )
                
                broadcast_result = await broadcast_response.json()
                if 'choices' in broadcast_result:
                    broadcast_script = broadcast_result["choices"][0]["message"]["content"].strip()
                elif 'response' in broadcast_result:
                    broadcast_script = broadcast_result["response"].strip()
                else:
                    print(f"API响应格式异常: {broadcast_result}")
                    return None

            # 保存播报稿
            with open(script_file, 'w', encoding='utf-8') as f:
                f.write(broadcast_script)

            # 生成音频
            audio_file = await self.generate_audio(broadcast_script, timestamp)
            if not audio_file:
                print("音频生成失败")
                audio_path = None
            else:
                print(f"音频生成成功: {audio_file}")
                audio_path = f'./podcasts/{timestamp}/podcast.mp3'

            # 更新索引
            podcast_data = {
                'id': timestamp,
                'date': datetime.now().strftime('%Y-%m-%d'),
                'title': f"出版电台播报 {datetime.now().strftime('%Y年%m月%d日')}",
                'transcript_path': f'./podcasts/{timestamp}/summary.html',  # 修改为HTML文件
                'audio_path': audio_path,  # 保持 ./ 前缀
                'highlight': highlight  # 添加广播式副标题
            }
            self.update_podcast_index(podcast_data)
            
            return summary_file

        except Exception as e:
            print(f"生成播报稿失败: {e}")
            import traceback
            print(traceback.format_exc())
            return None

async def main():
    """主函数"""
    generator = PodcastGenerator()
    
    # 1. 获取文章
    articles = generator.fetch_rss_articles()
    if not articles:
        print("未获取到文章")
        return
    
    # 2. 创建时间戳目录
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    podcast_dir = os.path.join(generator.podcasts_dir, timestamp)
    if not os.path.exists(podcast_dir):
        os.makedirs(podcast_dir)
        
    # 3. 并行总结文章
    summaries = await generator.summarize_articles(articles)
    if not summaries:
        print("文章总结失败")
        return
        
    # 4. 生成最终播报稿
    summary_file = await generator.generate_final_summary(summaries, timestamp)
    if not summary_file:
        print("生成播报稿失败")
        return
    
    print("\n处理完成!")
    print(f"文件已保存在: {summary_file}")

if __name__ == "__main__":
    
    # 运行异步主函数
    asyncio.run(main()) 
