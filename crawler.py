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
# Whirlpool Exblog 一次性全量爬虫
# ============================================================

BASE_URL = "http://wpblog.exblog.jp/"
DOMAIN = "wpblog.exblog.jp"

JSON_FILE = "articles.json"
URL_FILE = "article_urls.json"
IMAGE_DIR = "images"

REQUEST_TIMEOUT = 30
PAGE_DELAY = 0.5
IMAGE_DELAY = 0.3

DOWNLOAD_IMAGES = True


# ============================================================
# HTTP Header
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# URL标准化
# ============================================================

def normalize_url(url):
    """
    将URL标准化为：

    http://wpblog.exblog.jp/12345678/

    Exblog博客页面统一优先使用HTTP。
    """

    if not url:
        return None

    url = url.strip()

    # 相对URL
    url = urljoin(BASE_URL, url)

    parsed = urlparse(url)

    if parsed.netloc.lower() != DOMAIN:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    # 只保留path
    path = parsed.path

    if not path:
        return BASE_URL

    # 文章URL
    if re.fullmatch(r"/\d+/?", path):

        return (
            "http://"
            + DOMAIN
            + path.rstrip("/")
            + "/"
        )

    # 普通列表/归档页面
    return (
        "http://"
        + DOMAIN
        + path
    )


# ============================================================
# 判断文章URL
# ============================================================

def is_article_url(url):

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
# 请求页面
# ============================================================

def fetch(url):
    """
    优先HTTP。

    如果HTTP失败，再尝试HTTPS。
    """

    urls_to_try = []

    parsed = urlparse(url)

    http_url = (
        "http://"
        + parsed.netloc
        + parsed.path
    )

    if parsed.query:
        http_url += "?" + parsed.query

    https_url = (
        "https://"
        + parsed.netloc
        + parsed.path
    )

    if parsed.query:
        https_url += "?" + parsed.query

    # HTTP优先
    urls_to_try.append(http_url)

    # HTTPS作为备用
    if https_url != http_url:
        urls_to_try.append(https_url)


    last_error = None


    for target_url in urls_to_try:

        try:

            response = session.get(
                target_url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True
            )

            response.raise_for_status()

            # Exblog日文页面
            response.encoding = (
                response.apparent_encoding
                or "utf-8"
            )

            return response.text


        except Exception as e:

            last_error = e

            print(
                f"访问失败，尝试下一个地址:\n"
                f"{target_url}\n"
                f"{e}"
            )


    print(
        f"\n页面最终访问失败:\n"
        f"{url}\n"
        f"错误: {last_error}\n"
    )

    return None


# ============================================================
# 发现文章URL
# ============================================================

def discover_article_urls(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    urls = set()


    # --------------------------------------------------------
    # 1. 所有 <a href>
    # --------------------------------------------------------

    for a in soup.find_all("a"):

        href = a.get("href")

        if not href:
            continue

        full_url = normalize_url(
            href
        )

        if (
            full_url
            and
            is_article_url(full_url)
        ):

            urls.add(full_url)


    # --------------------------------------------------------
    # 2. RDF
    #
    # rdf:about
    # dc:identifier
    # --------------------------------------------------------

    for tag in soup.find_all():

        for attr_name in (
            "rdf:about",
            "about",
            "dc:identifier"
        ):

            value = tag.get(
                attr_name
            )

            if not value:
                continue

            full_url = normalize_url(
                value
            )

            if (
                full_url
                and
                is_article_url(full_url)
            ):

                urls.add(full_url)


    # --------------------------------------------------------
    # 3. 从原始HTML源码直接提取
    #
    # 防止RDF/XML被BeautifulSoup解析失败。
    # --------------------------------------------------------

    matches = re.findall(
        r'https?://wpblog\.exblog\.jp/\d+/?',
        html
    )

    for match in matches:

        full_url = normalize_url(
            match
        )

        if (
            full_url
            and
            is_article_url(full_url)
        ):

            urls.add(full_url)


    # --------------------------------------------------------
    # 4. 相对文章URL
    # --------------------------------------------------------

    matches = re.findall(
        r'(?<![\w-])/\d{7,9}/?',
        html
    )

    for match in matches:

        full_url = normalize_url(
            match
        )

        if (
            full_url
            and
            is_article_url(full_url)
        ):

            urls.add(full_url)


    return urls


# ============================================================
# 发现历史页面 / 分页
# ============================================================

def discover_navigation_urls(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    urls = set()


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
        "NEXT",
        "BACK",
    ]


    for a in soup.find_all("a"):

        href = a.get("href")

        if not href:
            continue

        full_url = urljoin(
            BASE_URL,
            href
        )

        parsed = urlparse(
            full_url
        )

        if (
            parsed.netloc.lower()
            != DOMAIN
        ):
            continue

        if is_article_url(
            full_url
        ):
            continue

        text = a.get_text(
            " ",
            strip=True
        )

        if any(
            keyword in text
            for keyword in navigation_keywords
        ):

            normalized = normalize_url(
                full_url
            )

            if normalized:

                urls.add(
                    normalized
                )


    return urls


# ============================================================
# 日期
# ============================================================

def normalize_date(date_text):

    if not date_text:
        return ""

    date_text = " ".join(
        date_text.split()
    )

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
# 正文提取
# ============================================================

def extract_content(body):

    if not body:

        return ""


    # 复制正文
    body_copy = BeautifulSoup(
        str(body),
        "html.parser"
    )


    # --------------------------------------------------------
    # 删除结构性 <br class="clear">
    # --------------------------------------------------------

    for br in body_copy.select(
        "br.clear"
    ):

        br.decompose()


    # --------------------------------------------------------
    # 删除图片
    # --------------------------------------------------------

    for img in body_copy.find_all(
        "img"
    ):

        img.decompose()


    # --------------------------------------------------------
    # 删除只剩空内容的<a>
    # --------------------------------------------------------

    for a in body_copy.find_all(
        "a"
    ):

        if not a.get_text(
            " ",
            strip=True
        ):

            a.decompose()


    # --------------------------------------------------------
    # 提取文本
    # --------------------------------------------------------

    content = body_copy.get_text(
        "\n",
        strip=True
    )


    # --------------------------------------------------------
    # 清理连续空行
    # --------------------------------------------------------

    lines = []

    for line in content.splitlines():

        line = line.strip()

        if line:

            lines.append(
                line
            )


    return "\n".join(
        lines
    )


# ============================================================
# 图片URL
# ============================================================

def extract_image_urls(body):

    if not body:

        return []


    image_urls = []


    for img in body.find_all(
        "img"
    ):

        src = (
            img.get("src")
            or
            img.get("data-src")
            or
            img.get("data-original")
        )


        # ----------------------------------------------------
        # 优先取图片外层<a>的href
        # ----------------------------------------------------

        parent_a = img.find_parent(
            "a"
        )


        if parent_a:

            href = parent_a.get(
                "href"
            )

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


        # ----------------------------------------------------
        # 没有<a>则使用img src
        # ----------------------------------------------------

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
# 图片下载
# ============================================================

def download_image(
    image_url,
    filename
):

    parsed = urlparse(
        image_url
    )


    urls_to_try = [
        image_url
    ]


    # HTTPS失败后尝试HTTP
    if parsed.scheme == "https":

        http_url = (
            "http://"
            + parsed.netloc
            + parsed.path
        )

        if parsed.query:

            http_url += (
                "?"
                +
                parsed.query
            )

        urls_to_try.append(
            http_url
        )


    last_error = None


    for target_url in urls_to_try:

        try:

            response = session.get(
                target_url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True
            )

            response.raise_for_status()


            # ------------------------------------------------
            # 判断图片扩展名
            # ------------------------------------------------

            final_path = urlparse(
                response.url
            ).path

            ext = os.path.splitext(
                final_path
            )[1].lower()


            if ext not in (
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".webp",
                ".bmp"
            ):

                content_type = (
                    response.headers
                    .get(
                        "Content-Type",
                        ""
                    )
                    .lower()
                )


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

            last_error = e


    print(
        "\n图片下载失败:"
    )

    print(
        image_url
    )

    print(
        last_error
    )


    return None


# ============================================================
# 单篇文章
# ============================================================

def crawl_article(url):

    html = fetch(
        url
    )


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
            f"\n警告：正文不存在:"
            f"\n{url}"
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
    # 下载图片
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
                IMAGE_DELAY
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
# 第一阶段：发现全部文章
# ============================================================

def discover_all_articles():

    print(
        "=" * 60
    )

    print(
        "第一阶段：发现文章"
    )

    print(
        "=" * 60
    )


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
        # 发现文章
        # ----------------------------------------------------

        found_articles = (
            discover_article_urls(
                html
            )
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
            f"篇，累计: "
            f"{len(article_urls)}"
        )


        # ----------------------------------------------------
        # 发现分页
        # ----------------------------------------------------

        navigation_urls = (
            discover_navigation_urls(
                html
            )
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

    print(
        "=" * 60
    )

    print(
        f"文章发现完成："
        f"{len(article_urls)} 篇"
    )

    print(
        f"扫描页面："
        f"{len(visited_pages)} 个"
    )

    print(
        "=" * 60
    )


    return sorted(
        article_urls
    )


# ============================================================
# 保存JSON
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

    article_urls = (
        discover_all_articles()
    )


    if not article_urls:

        print(
            "\n没有发现任何文章。"
        )

        return


    # --------------------------------------------------------
    # 保存URL列表
    # --------------------------------------------------------

    with open(
        URL_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            article_urls,
            f,
            ensure_ascii=False,
            indent=2
        )


    print(
        f"\n文章URL已经保存到："
        f"{URL_FILE}"
    )


    # ========================================================
    # 第二阶段
    # ========================================================

    print()

    print(
        "=" * 60
    )

    print(
        "第二阶段：抓取文章"
    )

    print(
        "=" * 60
    )

    print(
        f"总文章数："
        f"{len(article_urls)}"
    )


    articles = []


    for index, url in enumerate(
        tqdm(
            article_urls,
            desc="抓取文章"
        ),
        start=1
    ):

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

    print(
        "=" * 60
    )

    print(
        "全部完成"
    )

    print(
        "=" * 60
    )

    print(
        f"成功保存文章："
        f"{len(articles)} 篇"
    )

    print(
        f"JSON："
        f"{JSON_FILE}"
    )

    print(
        f"图片目录："
        f"{IMAGE_DIR}/"
    )


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    main()

