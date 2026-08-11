import json
import os
import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm


# ============================================================
# Exblog Whirlpool Blog 一次性全量爬虫
#
# 目标：
# https://wpblog.exblog.jp/
#
# 输出：
# articles.json
# images/
#     YYYYMMDD-01.jpg
#     YYYYMMDD-02.jpg
#     ...
#
# 已确认的文章页元素：
#   标题：h2.POST_TTL
#   日期：div.POST_TOP
#   正文：div.POST_BODY_SUB
#   图片：div.POST_BODY_SUB img
#
# 文章 URL：
#   https://wpblog.exblog.jp/数字/
#
# 发现方式：
#   1. 页面中的 Exblog 文章链接
#   2. RDF rdf:about / dc:identifier
#
# ============================================================


# ==========================
# 基本配置
# ==========================

BASE_URL = "https://wpblog.exblog.jp/"
DOMAIN = "wpblog.exblog.jp"

JSON_FILE = "articles.json"
IMAGE_DIR = "images"

REQUEST_TIMEOUT = 30

# 页面之间稍微等待，避免请求过快
PAGE_DELAY = 0.5

# 是否下载图片
DOWNLOAD_IMAGES = True


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


# ==========================
# Session
# ==========================

session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# URL工具
# ============================================================

def normalize_url(url):
    """
    标准化URL。
    """

    if not url:
        return None

    url = url.strip()

    # 相对URL转绝对URL
    url = urljoin(BASE_URL, url)

    parsed = urlparse(url)

    if parsed.netloc.lower() != DOMAIN:
        return None

    # 只保留 http/https
    if parsed.scheme not in ("http", "https"):
        return None

    # 去掉 query / fragment
    url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    # Exblog文章通常是 /数字/
    if not re.search(r"/\d+/?$", parsed.path):
        return None

    if not url.endswith("/"):
        url += "/"

    return url


# ============================================================
# 判断是否为文章 URL
# ============================================================

def is_article_url(url):
    """
    Exblog文章URL通常类似：

    https://wpblog.exblog.jp/12180930/
    """

    if not url:
        return False

    parsed = urlparse(url)

    if parsed.netloc.lower() != DOMAIN:
        return False

    return bool(
        re.fullmatch(
            r"/\d+/?",
            parsed.path
        )
    )


# ============================================================
# 获取网页
# ============================================================

def fetch(url):

    try:

        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        # Exblog日文页面
        response.encoding = response.apparent_encoding or "utf-8"

        return response.text

    except Exception as e:

        print(
            f"\n页面访问失败: {url}\n"
            f"错误: {e}\n"
        )

        return None


# ============================================================
# 从页面发现文章URL
# ============================================================

def discover_article_urls(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    urls = set()


    # --------------------------------------------------------
    # 方法1：
    # 页面所有 <a href>
    # --------------------------------------------------------

    for a in soup.find_all("a"):

        href = a.get("href")

        if not href:
            continue

        full_url = normalize_url(href)

        if full_url and is_article_url(full_url):

            urls.add(full_url)


    # --------------------------------------------------------
    # 方法2：
    # RDF中的 rdf:about
    #
    # 例如：
    #
    # <rdf:Description
    #   rdf:about="https://wpblog.exblog.jp/12180930/"
    #   dc:identifier="https://wpblog.exblog.jp/12180930/"
    # />
    # --------------------------------------------------------

    for tag in soup.find_all():

        for attr_name in (
            "rdf:about",
            "about",
            "dc:identifier"
        ):

            value = tag.get(attr_name)

            if not value:
                continue

            full_url = normalize_url(value)

            if full_url and is_article_url(full_url):

                urls.add(full_url)


    # --------------------------------------------------------
    # 方法3：
    # 直接从源码正则提取
    #
    # 防止某些 RDF/XML 没有被 BeautifulSoup
    # 正常解析
    # --------------------------------------------------------

    patterns = [

        r'https?://wpblog\.exblog\.jp/\d+/?',

        r'(?<![\w-])/(\d{7,9})/?'
    ]


    for pattern in patterns:

        for match in re.findall(
            pattern,
            html
        ):

            if pattern.startswith("https"):

                url = normalize_url(match)

            else:

                url = normalize_url(
                    "/" + match + "/"
                )

            if url and is_article_url(url):

                urls.add(url)


    return urls


# ============================================================
# 从页面发现“下一页”等列表页
# ============================================================

def discover_navigation_urls(html):

    """
    寻找可能的历史页面 / 下一页。

    这里不会把普通文章URL加入导航队列。
    """

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    urls = set()


    for a in soup.find_all("a"):

        href = a.get("href")

        if not href:
            continue

        full_url = urljoin(
            BASE_URL,
            href
        )

        parsed = urlparse(full_url)

        if parsed.netloc.lower() != DOMAIN:
            continue

        # 如果本身就是文章，不作为列表页继续遍历
        if is_article_url(full_url):
            continue

        # 只允许本站
        if parsed.scheme not in (
            "http",
            "https"
        ):
            continue

        full_url = (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
            f"{parsed.path}"
        )

        # 常见“下一页/旧文章/历史”文字
        text = a.get_text(
            " ",
            strip=True
        )

        navigation_keywords = [
            "次のページ",
            "前のページ",
            "次へ",
            "前へ",
            "Older",
            "Next",
            "Previous",
            "過去の記事",
            "過去ログ",
            "以前の記事",
            "新しい記事",
            "古い記事",
        ]

        if any(
            keyword in text
            for keyword in navigation_keywords
        ):

            urls.add(full_url)


    return urls


# ============================================================
# 日期处理
# ============================================================

def normalize_date(date_text):

    if not date_text:
        return ""

    date_text = " ".join(
        date_text.split()
    )

    # 例如：
    # 2009年 06月 04日
    # 2009年06月04日
    # 2009年 10月 22日

    match = re.search(
        r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日",
        date_text
    )

    if match:

        year = match.group(1)
        month = match.group(2).zfill(2)
        day = match.group(3).zfill(2)

        return (
            f"{year}年"
            f"{month}月"
            f"{day}日"
        )

    return date_text


# ============================================================
# 日期 → YYYYMMDD
# ============================================================

def date_to_filename(date_text):

    match = re.search(
        r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日",
        date_text
    )

    if not match:
        return "unknown"

    return (
        match.group(1)
        +
        match.group(2).zfill(2)
        +
        match.group(3).zfill(2)
    )


# ============================================================
# 清理正文
# ============================================================

def extract_content(body):

    if not body:
        return ""

    # 复制，避免破坏后面的图片提取
    body_copy = BeautifulSoup(
        str(body),
        "html.parser"
    )

    # Exblog的：
    # <br class="clear">
    #
    # 一般是结构性换行，不作为正文内容
    for br in body_copy.select(
        "br.clear"
    ):

        br.decompose()


    # 图片本身不需要出现在正文文本里
    for img in body_copy.find_all("img"):

        img.decompose()


    # 图片链接如果只包住图片，
    # 图片删除后会留下空a，也删除
    for a in body_copy.find_all("a"):

        if not a.get_text(
            " ",
            strip=True
        ):

            a.decompose()


    content = body_copy.get_text(
        "\n",
        strip=True
    )


    # 清理过多空行
    lines = []

    for line in content.splitlines():

        line = line.strip()

        if not line:
            continue

        lines.append(line)


    return "\n".join(lines)


# ============================================================
# 提取图片URL
# ============================================================

def extract_image_urls(body):

    if not body:
        return []

    image_urls = []


    for img in body.find_all("img"):

        src = (
            img.get("src")
            or
            img.get("data-src")
            or
            img.get("data-original")
        )


        # ----------------------------------------------------
        # 优先：
        #
        # <center>
        #   <a href="原图">
        #       <img ...>
        #   </a>
        # </center>
        #
        # 因为href通常可能是原图，而src可能是显示图片。
        # ----------------------------------------------------

        parent_a = img.find_parent("a")

        if parent_a:

            href = parent_a.get("href")

            if href:

                image_url = urljoin(
                    BASE_URL,
                    href
                )

                if image_url not in image_urls:

                    image_urls.append(
                        image_url
                    )

                continue


        if src:

            image_url = urljoin(
                BASE_URL,
                src
            )

            if image_url not in image_urls:

                image_urls.append(
                    image_url
                )


    return image_urls


# ============================================================
# 下载图片
# ============================================================

def download_image(
    image_url,
    filename
):

    try:

        response = session.get(
            image_url,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True
        )

        response.raise_for_status()


        # 根据最终URL判断扩展名
        path = urlparse(
            response.url
        ).path

        ext = os.path.splitext(
            path
        )[1].lower()


        # 如果URL没有扩展名，
        # 根据Content-Type判断
        if ext not in (
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".bmp"
        ):

            content_type = response.headers.get(
                "Content-Type",
                ""
            ).lower()

            if "png" in content_type:
                ext = ".png"

            elif "gif" in content_type:
                ext = ".gif"

            elif "webp" in content_type:
                ext = ".webp"

            else:
                ext = ".jpg"


        path = os.path.join(
            IMAGE_DIR,
            filename + ext
        )


        with open(
            path,
            "wb"
        ) as f:

            f.write(
                response.content
            )


        return os.path.basename(
            path
        )


    except Exception as e:

        print(
            f"\n图片下载失败:\n"
            f"{image_url}\n"
            f"{e}\n"
        )

        return None


# ============================================================
# 抓取单篇文章
# ============================================================

def crawl_article(url):

    html = fetch(url)

    if not html:

        return None


    soup = BeautifulSoup(
        html,
        "html.parser"
    )


    # --------------------------------------------------------
    # 标题
    # --------------------------------------------------------

    title_element = soup.select_one(
        "h2.POST_TTL"
    )


    title = ""

    if title_element:

        title = title_element.get_text(
            " ",
            strip=True
        )


    # --------------------------------------------------------
    # 日期
    # --------------------------------------------------------

    date_element = soup.select_one(
        "div.POST_TOP"
    )


    date = ""

    if date_element:

        date = normalize_date(
            date_element.get_text(
                " ",
                strip=True
            )
        )


    # --------------------------------------------------------
    # 正文
    # --------------------------------------------------------

    body = soup.select_one(
        "div.POST_BODY_SUB"
    )


    if not body:

        print(
            f"\n警告：没有找到正文: {url}"
        )

        content = ""

        image_urls = []

    else:

        content = extract_content(
            body
        )

        image_urls = extract_image_urls(
            body
        )


    # --------------------------------------------------------
    # 图片
    # --------------------------------------------------------

    images = []


    if DOWNLOAD_IMAGES:

        date_key = date_to_filename(
            date
        )


        for index, image_url in enumerate(
            image_urls,
            start=1
        ):

            filename = (
                f"{date_key}-"
                f"{index:02d}"
            )


            saved = download_image(
                image_url,
                filename
            )


            if saved:

                images.append(
                    {
                        "file": saved,
                        "url": image_url
                    }
                )


            time.sleep(
                0.3
            )


    else:

        images = [
            {
                "file": "",
                "url": image_url
            }
            for image_url in image_urls
        ]


    return {
        "url": url,
        "title": title,
        "date": date,
        "content": content,
        "images": images
    }


# ============================================================
# 第一阶段：
# 从首页开始发现全部文章
# ============================================================

def discover_all_articles():

    print("=" * 60)
    print("第一阶段：发现文章")
    print("=" * 60)


    queue = deque()

    queue.append(
        BASE_URL
    )


    visited_pages = set()

    article_urls = set()


    while queue:

        page_url = queue.popleft()


        if page_url in visited_pages:

            continue


        visited_pages.add(
            page_url
        )


        print(
            f"\n扫描页面: {page_url}"
        )


        html = fetch(
            page_url
        )


        if not html:

            continue


        # ----------------------------------------------------
        # 找文章
        # ----------------------------------------------------

        found_articles = discover_article_urls(
            html
        )


        old_count = len(
            article_urls
        )


        article_urls.update(
            found_articles
        )


        new_count = (
            len(article_urls)
            -
            old_count
        )


        print(
            f"发现文章: {new_count} "
            f"篇，累计: {len(article_urls)}"
        )


        # ----------------------------------------------------
        # 找历史/分页页面
        # ----------------------------------------------------

        navigation_urls = discover_navigation_urls(
            html
        )


        for nav_url in navigation_urls:

            if nav_url not in visited_pages:

                queue.append(
                    nav_url
                )


        time.sleep(
            PAGE_DELAY
        )


    print()
    print("=" * 60)
    print(
        f"文章发现完成：{len(article_urls)} 篇"
    )
    print(
        f"扫描页面：{len(visited_pages)} 个"
    )
    print("=" * 60)


    return sorted(
        article_urls
    )


# ============================================================
# 保存 JSON
# ============================================================

def save_json(
    articles
):

    with open(
        JSON_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            articles,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# 主程序
# ============================================================

def main():

    os.makedirs(
        IMAGE_DIR,
        exist_ok=True
    )


    # ========================================================
    # 第一阶段
    # ========================================================

    article_urls = discover_all_articles()


    if not article_urls:

        print(
            "没有发现任何文章。"
        )

        return


    # 保存发现结果
    with open(
        "article_urls.json",
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            article_urls,
            f,
            ensure_ascii=False,
            indent=2
        )


    # ========================================================
    # 第二阶段
    # ========================================================

    print()
    print("=" * 60)
    print("第二阶段：抓取文章")
    print("=" * 60)

    print(
        f"总文章数：{len(article_urls)}"
    )


    articles = []


    for index, url in enumerate(
        tqdm(article_urls),
        start=1
    ):

        print(
            f"\n处理 [{index}/{len(article_urls)}]"
        )

        print(
            url
        )


        article = crawl_article(
            url
        )


        if article:

            articles.append(
                article
            )


        time.sleep(
            PAGE_DELAY
        )


    # ========================================================
    # 保存
    # ========================================================

    save_json(
        articles
    )


    print()
    print("=" * 60)
    print("全部完成")
    print("=" * 60)

    print(
        f"成功保存文章：{len(articles)} 篇"
    )

    print(
        f"JSON：{JSON_FILE}"
    )

    print(
        f"图片目录：{IMAGE_DIR}/"
    )


if __name__ == "__main__":

    main()
