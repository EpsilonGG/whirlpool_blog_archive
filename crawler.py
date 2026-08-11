from __future__ import annotations

import json
import logging
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

EXTRA_ARTICLE_URLS = [
    "https://whirlpool.co.jp/release/blog/20251120/",
    "https://whirlpool.co.jp/release/blog/watch-and-download-movie-glass-2019/",
]

# ============================================================
# Configuration
# ============================================================

BASE_URL = "https://whirlpool.co.jp"
BLOG_URL = f"{BASE_URL}/release/blog/"

OUTPUT_FILE = Path("articles.json")
IMAGE_DIR = Path("images")

REQUEST_DELAY = 0.8
REQUEST_TIMEOUT = 30

# 防止以后博客分页超过当前数量
MAX_PAGES = 100

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("whirlpool-crawler")


# ============================================================
# HTTP Session
# ============================================================

def create_session() -> requests.Session:
    session = requests.Session()

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1,
        status_forcelist=[
            429,
            500,
            502,
            503,
            504,
        ],
        allowed_methods=[
            "GET",
        ],
        raise_on_status=False,
    )

    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=5,
        pool_maxsize=5,
    )

    session.mount(
        "http://",
        adapter,
    )

    session.mount(
        "https://",
        adapter,
    )

    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en;q=0.8",
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
        }
    )

    return session


session = create_session()


# ============================================================
# HTTP
# ============================================================

def request_get(url: str) -> requests.Response:
    """
    GET 请求。
    """

    logger.debug(
        "GET %s",
        url,
    )

    response = session.get(
        url,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    time.sleep(
        REQUEST_DELAY
    )

    return response


def get_soup(url: str) -> BeautifulSoup:
    """
    使用 Python 内置 html.parser。
    不依赖 lxml。
    """

    response = request_get(url)

    return BeautifulSoup(
        response.content,
        "html.parser",
    )


# ============================================================
# Text utilities
# ============================================================

def clean_text(text: str) -> str:
    """
    清理 HTML 文本。

    尽量保留博客原来的段落和换行。
    """

    text = text.replace(
        "\r\n",
        "\n",
    )

    text = text.replace(
        "\r",
        "\n",
    )

    # NBSP
    text = text.replace(
        "\xa0",
        " ",
    )

    lines = []

    for line in text.split("\n"):

        # 去掉每一行首尾空格
        line = line.strip()

        if line:
            lines.append(line)
        else:
            lines.append("")

    text = "\n".join(lines)

    # 最多连续两个空行
    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


# ============================================================
# URL utilities
# ============================================================

def normalize_url(url: str) -> str:
    """
    URL 规范化：

    https://example.com/test/?a=1#abc

    ↓

    https://example.com/test/
    """

    parsed = urlparse(url)

    path = parsed.path.rstrip("/") + "/"

    return (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
        f"{path}"
    )


def get_article_id(url: str) -> str:
    """
    从：

    /release/blog/blog20260807/

    获取：

    blog20260807
    """

    match = re.search(
        r"/(blog\d{8})/?$",
        url,
    )

    if match:
        return match.group(1)

    return url.rstrip("/").split("/")[-1]


# ============================================================
# Pagination
# ============================================================

def get_page_url(page: int) -> str:

    if page == 1:
        return BLOG_URL

    return (
        f"{BLOG_URL}"
        f"page/{page}/"
    )


def is_blog_page(
    soup: BeautifulSoup,
) -> bool:

    """
    判断是不是 Whirlpool Staff Blog 页面。
    """

    text = soup.get_text(
        " ",
        strip=True,
    )

    return (
        "スタッフ日誌" in text
    )


def discover_pages() -> list[str]:
    """
    自动寻找所有分页。

    不写死 9 页。
    """

    pages = []

    consecutive_failures = 0

    for page_number in range(
        1,
        MAX_PAGES + 1,
    ):

        url = get_page_url(
            page_number
        )

        logger.info(
            "检查目录页：第 %d 页",
            page_number,
        )

        try:

            soup = get_soup(
                url
            )

            if not is_blog_page(
                soup
            ):

                logger.warning(
                    "页面不是 Staff Blog：%s",
                    url,
                )

                consecutive_failures += 1

                if consecutive_failures >= 2:
                    break

                continue

            pages.append(
                url
            )

            consecutive_failures = 0

        except Exception as exc:

            logger.warning(
                "目录页请求失败：%s | %s",
                url,
                exc,
            )

            consecutive_failures += 1

            if consecutive_failures >= 2:
                break

    logger.info(
        "共发现 %d 个 Blog 目录页",
        len(pages),
    )

    return pages


# ============================================================
# Article discovery
# ============================================================

def is_article_url(
    url: str,
) -> bool:

    parsed = urlparse(url)

    if parsed.netloc:
        if parsed.netloc.lower() != "whirlpool.co.jp":
            return False

    path = parsed.path.rstrip("/")

    return bool(
        re.match(
            r"^/release/blog/blog\d{8}$",
            path,
        )
    )


def discover_article_urls(
    page_url: str,
) -> list[str]:

    soup = get_soup(
        page_url
    )

    urls = []

    for link in soup.find_all(
        "a",
        href=True,
    ):

        href = link.get(
            "href"
        )

        if not href:
            continue

        url = urljoin(
            page_url,
            href,
        )

        url = normalize_url(
            url
        )

        if is_article_url(
            url
        ):

            if url not in urls:
                urls.append(
                    url
                )

    return urls


def discover_all_articles() -> list[str]:

    pages = discover_pages()

    all_urls = []

    for index, page_url in enumerate(
        pages,
        start=1,
    ):

        logger.info(
            "[目录 %d/%d] %s",
            index,
            len(pages),
            page_url,
        )

        try:

            urls = discover_article_urls(
                page_url
            )

            logger.info(
                "发现 %d 篇文章",
                len(urls),
            )

            all_urls.extend(
                urls
            )

        except Exception as exc:

            logger.exception(
                "目录扫描失败：%s",
                page_url,
            )

    # 全局去重
    all_urls = list(
        dict.fromkeys(
            all_urls
        )
    )

    logger.info(
        "总计发现 %d 篇文章",
        len(all_urls),
    )

    return all_urls


# ============================================================
# Article parsing
# ============================================================

def extract_title(
    soup: BeautifulSoup,
) -> str:

    """
    实际 DOM：

    <article class="common-container">
        <h4>标题</h4>
    </article>
    """

    article = soup.select_one(
        "article.common-container"
    )

    if article:

        h4 = article.find(
            "h4"
        )

        if h4:

            return clean_text(
                h4.get_text(
                    "\n",
                    strip=True,
                )
            )

    # fallback
    h4 = soup.find(
        "h4"
    )

    if h4:

        return clean_text(
            h4.get_text(
                "\n",
                strip=True,
            )
        )

    return ""


def extract_date(
    soup: BeautifulSoup,
) -> str:

    """
    实际 DOM：

    <p class="days en">
        2026.08.07 UpDate
    </p>

    输出：

    2026年08月07日
    """

    node = soup.select_one(
        "p.days.en"
    )

    if not node:
        return ""

    text = clean_text(
        node.get_text(
            " ",
            strip=True,
        )
    )

    match = re.search(
        r"(20\d{2})\.(\d{1,2})\.(\d{1,2})",
        text,
    )

    if not match:
        return text

    year, month, day = match.groups()

    return (
        f"{year}年"
        f"{int(month):02d}月"
        f"{int(day):02d}日"
    )


def extract_content(
    soup: BeautifulSoup,
) -> str:

    """
    实际 DOM：

    <div class="main-text">
        ...
    </div>
    """

    node = soup.select_one(
        "div.main-text"
    )

    if not node:
        return ""

    # --------------------------------------------------------
    # 处理 <br>
    #
    # BeautifulSoup 的 get_text("\n") 对部分 HTML
    # 的换行处理不够稳定。
    #
    # 所以先把 <br> 显式转换成换行。
    # --------------------------------------------------------

    for br in node.find_all(
        "br"
    ):

        br.replace_with(
            "\n"
        )

    # --------------------------------------------------------
    # 获取正文
    # --------------------------------------------------------

    text = node.get_text(
        "\n",
        strip=False,
    )

    return clean_text(
        text
    )


# ============================================================
# Image parsing
# ============================================================

def extract_image_urls(
    soup: BeautifulSoup,
    article_url: str,
) -> list[str]:

    """
    实际 DOM：

    <div class="image-box">
        <img src="https://...">
    </div>
    """

    image_urls = []

    boxes = soup.select(
        "div.image-box"
    )

    for box in boxes:

        for img in box.find_all(
            "img"
        ):

            src = img.get(
                "src"
            )

            if not src:
                continue

            image_url = urljoin(
                article_url,
                src,
            )

            parsed = urlparse(
                image_url
            )

            image_url = (
                f"{parsed.scheme}://"
                f"{parsed.netloc}"
                f"{parsed.path}"
            )

            if image_url not in image_urls:

                image_urls.append(
                    image_url
                )

    return image_urls


# ============================================================
# Image downloading
# ============================================================

def get_image_extension(
    url: str,
    response: requests.Response,
) -> str:

    # 先从 URL 判断
    path = urlparse(
        url
    ).path

    suffix = Path(
        path
    ).suffix.lower()

    valid = {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".bmp",
        ".svg",
        ".avif",
    }

    if suffix in valid:
        return suffix

    # 再从 Content-Type 判断
    content_type = (
        response.headers
        .get(
            "Content-Type",
            "",
        )
        .split(";")[0]
        .strip()
        .lower()
    )

    extension = mimetypes.guess_extension(
        content_type
    )

    if extension:
        return extension

    return ".bin"


def download_images(
    image_urls: list[str],
    article_id: str,
) -> list[str]:

    if not image_urls:
        return []

    article_dir = (
        IMAGE_DIR
        / article_id
    )

    article_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    local_images = []

    for index, image_url in enumerate(
        image_urls,
        start=1,
    ):

        try:

            logger.info(
                "    图片 [%d/%d] %s",
                index,
                len(image_urls),
                image_url,
            )

            response = request_get(
                image_url
            )

            content_type = (
                response.headers
                .get(
                    "Content-Type",
                    "",
                )
                .lower()
            )

            # 防止服务器返回 HTML 错误页面
            if (
                content_type
                and not content_type.startswith(
                    "image/"
                )
            ):

                logger.warning(
                    "    非图片资源，跳过：%s",
                    content_type,
                )

                continue

            extension = get_image_extension(
                image_url,
                response,
            )

            filename = (
                f"{index:03d}"
                f"{extension}"
            )

            path = (
                article_dir
                / filename
            )

            path.write_bytes(
                response.content
            )

            local_images.append(
                path.as_posix()
            )

        except Exception as exc:

            logger.warning(
                "    图片下载失败：%s | %s",
                image_url,
                exc,
            )

    return local_images


# ============================================================
# Single article
# ============================================================

def crawl_article(
    url: str,
) -> dict:

    logger.info(
        "抓取文章：%s",
        url,
    )

    soup = get_soup(
        url
    )

    article_id = get_article_id(
        url
    )

    title = extract_title(
        soup
    )

    date = extract_date(
        soup
    )

    content = extract_content(
        soup
    )

    image_urls = extract_image_urls(
        soup,
        url,
    )

    logger.info(
        "    标题：%s",
        title,
    )

    logger.info(
        "    日期：%s",
        date,
    )

    logger.info(
        "    正文长度：%d",
        len(content),
    )

    logger.info(
        "    图片数量：%d",
        len(image_urls),
    )

    # 下载图片
    local_images = download_images(
        image_urls,
        article_id,
    )

    return {
        "url": url,
        "title": title,
        "date": date,
        "content": content,
        "images": local_images,
    }


# ============================================================
# Existing JSON
# ============================================================

def load_articles() -> list[dict]:

    if not OUTPUT_FILE.exists():
        return []

    try:

        data = json.loads(
            OUTPUT_FILE.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(
            data,
            list,
        ):
            return data

    except Exception as exc:

        logger.warning(
            "读取 articles.json 失败：%s",
            exc,
        )

    return []


def save_articles(
    articles: list[dict],
) -> None:

    # 日期倒序
    articles.sort(
        key=lambda x: x.get(
            "date",
            "",
        ),
        reverse=True,
    )

    OUTPUT_FILE.write_text(
        json.dumps(
            articles,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# Main
# ============================================================
def add_extra_articles() -> None:
    """
    只抓取 EXTRA_ARTICLE_URLS 中指定的文章。

    不扫描目录页。
    不重新抓取已有文章。
    不删除任何已有数据。
    """

    logger.info("=" * 70)
    logger.info("补充指定 Whirlpool Blog 文章")
    logger.info("=" * 70)

    articles = load_articles()

    existing_urls = {
        normalize_url(article.get("url", ""))
        for article in articles
        if article.get("url")
    }

    added = 0
    skipped = 0
    failed = 0

    for url in EXTRA_ARTICLE_URLS:

        url = normalize_url(url)

        logger.info("")
        logger.info("处理：%s", url)

        # ----------------------------------------------------
        # 已经存在
        # ----------------------------------------------------

        if url in existing_urls:

            logger.info(
                "已经存在，跳过：%s",
                url,
            )

            skipped += 1
            continue

        # ----------------------------------------------------
        # 抓取
        # ----------------------------------------------------

        try:

            article = crawl_article(url)

            if article is None:

                logger.warning(
                    "页面不是有效 Blog 文章：%s",
                    url,
                )

                failed += 1
                continue

            articles.append(article)

            existing_urls.add(url)

            added += 1

            logger.info(
                "成功添加：%s",
                article.get("title", ""),
            )

        except Exception as exc:

            failed += 1

            logger.exception(
                "添加失败：%s",
                url,
            )

    # --------------------------------------------------------
    # 只有真的新增文章时才保存
    # --------------------------------------------------------

    if added > 0:

        save_articles(
            articles
        )

        logger.info(
            "已保存 articles.json"
        )

    else:

        logger.info(
            "没有新增文章，不修改 articles.json"
        )

    logger.info("")
    logger.info("=" * 70)
    logger.info("补充完成")
    logger.info("=" * 70)

    logger.info(
        "新增：%d",
        added,
    )

    logger.info(
        "已存在：%d",
        skipped,
    )

    logger.info(
        "失败：%d",
        failed,
    )

    logger.info(
        "当前文章总数：%d",
        len(articles),
    )

    logger.info(
        "=" * 70
    )
def main():

    logger.info(
        "=" * 70
    )

    logger.info(
        "Whirlpool Staff Blog Crawler"
    )

    logger.info(
        "=" * 70
    )

    IMAGE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # 1. 发现文章
    # --------------------------------------------------------

    article_urls = (
        discover_all_articles()
    )

    if not article_urls:

        logger.error(
            "没有发现任何文章。"
        )

        raise SystemExit(1)

    # --------------------------------------------------------
    # 2. 读取历史结果
    # --------------------------------------------------------

    articles = load_articles()

    existing_urls = {
        article.get(
            "url"
        )
        for article in articles
        if article.get(
            "url"
        )
    }

    logger.info(
        "已有文章：%d 篇",
        len(existing_urls),
    )

    # --------------------------------------------------------
    # 3. 全量抓取
    # --------------------------------------------------------

    success = 0
    skipped = 0
    failed = 0

    total = len(
        article_urls
    )

    for index, url in enumerate(
        article_urls,
        start=1,
    ):

        logger.info(
            ""
        )

        logger.info(
            "============================================================"
        )

        logger.info(
            "[%d/%d]",
            index,
            total,
        )

        # ----------------------------------------------------
        # 已抓取
        # ----------------------------------------------------

        if url in existing_urls:

            logger.info(
                "已存在，跳过：%s",
                url,
            )

            skipped += 1

            continue

        # ----------------------------------------------------
        # 抓取
        # ----------------------------------------------------

        try:

            article = crawl_article(
                url
            )

            # 防止重复
            articles = [
                item
                for item in articles
                if item.get(
                    "url"
                ) != url
            ]

            articles.append(
                article
            )

            # 每篇完成立即保存
            save_articles(
                articles
            )

            success += 1

            logger.info(
                "抓取成功：%s",
                article.get(
                    "title",
                    "",
                ),
            )

        except Exception as exc:

            failed += 1

            logger.exception(
                "抓取失败：%s",
                url,
            )

    # --------------------------------------------------------
    # 4. 最终保存
    # --------------------------------------------------------

    save_articles(
        articles
    )

    # --------------------------------------------------------
    # 5. Summary
    # --------------------------------------------------------

    logger.info(
        ""
    )

    logger.info(
        "=" * 70
    )

    logger.info(
        "全部任务完成"
    )

    logger.info(
        "=" * 70
    )

    logger.info(
        "发现文章：%d",
        total,
    )

    logger.info(
        "本次成功：%d",
        success,
    )

    logger.info(
        "跳过已有：%d",
        skipped,
    )

    logger.info(
        "失败：%d",
        failed,
    )

    logger.info(
        "JSON：%s",
        OUTPUT_FILE,
    )

    logger.info(
        "图片目录：%s",
        IMAGE_DIR,
    )

    logger.info(
        "=" * 70
    )


if __name__ == "__main__":

    import sys

    if "--add-missing" in sys.argv:
        add_extra_articles()
    else:
        main()
