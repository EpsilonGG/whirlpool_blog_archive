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


# ============================================================
# Configuration
# ============================================================

BASE_URL = "https://whirlpool.co.jp"

ARTICLES_FILE = Path("articles.json")
IMAGE_DIR = Path("images")

REQUEST_DELAY = 0.8
REQUEST_TIMEOUT = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


# ============================================================
# 需要补充的两个文章
# ============================================================

TARGET_ARTICLES = [
    "https://whirlpool.co.jp/release/blog/20251120/",
    "https://whirlpool.co.jp/release/blog/watch-and-download-movie-glass-2019/",
]


# ============================================================
# Logging
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("whirlpool-add-articles")


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

def get(
    url: str,
) -> requests.Response:

    logger.info(
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


def get_soup(
    url: str,
) -> BeautifulSoup:

    response = get(
        url
    )

    # 使用 Python 内置解析器
    # 不需要安装 lxml
    return BeautifulSoup(
        response.content,
        "html.parser",
    )


# ============================================================
# Utilities
# ============================================================

def clean_text(
    text: str,
) -> str:

    text = text.replace(
        "\r\n",
        "\n",
    )

    text = text.replace(
        "\r",
        "\n",
    )

    text = text.replace(
        "\xa0",
        " ",
    )

    # 清理每行首尾空白
    lines = []

    for line in text.split("\n"):

        line = line.strip()

        if line:
            lines.append(line)
        else:
            lines.append("")

    text = "\n".join(
        lines
    )

    # 最多保留一个空行
    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


def normalize_url(
    url: str,
) -> str:

    parsed = urlparse(
        url
    )

    path = parsed.path.rstrip(
        "/"
    )

    if not path:
        path = "/"

    return (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
        f"{path}/"
    )


def get_article_id(
    url: str,
) -> str:

    path = urlparse(
        url
    ).path.rstrip(
        "/"
    )

    name = path.split(
        "/"
    )[-1]

    if not name:
        name = "article"

    # URL 中的特殊字符替换为 _
    name = re.sub(
        r"[^\w\u3040-\u30ff\u3400-\u9fff-]",
        "_",
        name,
    )

    return name


# ============================================================
# Load existing articles
# ============================================================

def load_articles() -> list[dict]:

    if not ARTICLES_FILE.exists():

        logger.error(
            "找不到 %s",
            ARTICLES_FILE,
        )

        raise SystemExit(1)

    try:

        data = json.loads(
            ARTICLES_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:

        logger.error(
            "读取 articles.json 失败：%s",
            exc,
        )

        raise SystemExit(1)

    if not isinstance(
        data,
        list,
    ):

        logger.error(
            "articles.json 顶层结构不是数组。"
        )

        raise SystemExit(1)

    logger.info(
        "读取现有文章：%d 篇",
        len(data),
    )

    return data


# ============================================================
# Extract title
# ============================================================

def extract_title(
    soup: BeautifulSoup,
) -> str:

    # 你的实际 DOM：
    #
    # <article class="common-container">
    #     <h4>标题</h4>

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


# ============================================================
# Extract date
# ============================================================

def extract_date(
    soup: BeautifulSoup,
) -> str:

    # 实际 DOM：
    #
    # <p class="days en">
    #     2026.08.07 UpDate
    # </p>

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


# ============================================================
# Extract content
# ============================================================

def extract_content(
    soup: BeautifulSoup,
) -> str:

    # 实际 DOM：
    #
    # <div class="main-text">
    #     ...
    # </div>

    node = soup.select_one(
        "div.main-text"
    )

    if not node:
        return ""

    # --------------------------------------------------------
    # 将 <br> 转换成真正的换行
    # --------------------------------------------------------

    for br in node.find_all(
        "br"
    ):

        br.replace_with(
            "\n"
        )

    text = node.get_text(
        "\n",
        strip=False,
    )

    return clean_text(
        text
    )


# ============================================================
# Extract images
# ============================================================

def extract_image_urls(
    soup: BeautifulSoup,
    article_url: str,
) -> list[str]:

    image_urls = []

    # 实际 DOM：
    #
    # <div class="image-box">
    #     <img src="...">
    # </div>

    for box in soup.select(
        "div.image-box"
    ):

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

            # 去掉 query / fragment
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
# Image extension
# ============================================================

def get_image_extension(
    url: str,
    response: requests.Response,
) -> str:

    path = urlparse(
        url
    ).path

    suffix = Path(
        path
    ).suffix.lower()

    valid_extensions = {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".bmp",
        ".svg",
        ".avif",
    }

    if suffix in valid_extensions:

        return suffix

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


# ============================================================
# Download images for ONE article
# ============================================================

def download_images(
    image_urls: list[str],
    article_id: str,
) -> list[str]:

    if not image_urls:
        logger.info(
            "    没有图片"
        )

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

        logger.info(
            "    下载图片 [%d/%d]",
            index,
            len(image_urls),
        )

        logger.info(
            "    %s",
            image_url,
        )

        try:

            response = get(
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
                    "    不是图片，跳过：%s",
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

            output_path = (
                article_dir
                / filename
            )

            output_path.write_bytes(
                response.content
            )

            local_images.append(
                output_path.as_posix()
            )

            logger.info(
                "    保存：%s",
                output_path,
            )

        except Exception as exc:

            logger.warning(
                "    图片下载失败：%s",
                exc,
            )

    return local_images


# ============================================================
# Crawl ONE specified article
# ============================================================

def crawl_article(
    url: str,
) -> dict:

    url = normalize_url(
        url
    )

    logger.info(
        ""
    )

    logger.info(
        "=" * 70
    )

    logger.info(
        "抓取指定文章"
    )

    logger.info(
        "%s",
        url,
    )

    logger.info(
        "=" * 70
    )

    soup = get_soup(
        url
    )

    # --------------------------------------------------------
    # 验证页面是否具有正文
    # --------------------------------------------------------

    main_text = soup.select_one(
        "div.main-text"
    )

    if main_text is None:

        raise RuntimeError(
            "页面中找不到 div.main-text，"
            "无法确认这是目标 Blog 文章。"
        )

    # --------------------------------------------------------
    # 提取字段
    # --------------------------------------------------------

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
        "标题：%s",
        title,
    )

    logger.info(
        "日期：%s",
        date,
    )

    logger.info(
        "正文长度：%d 字",
        len(content),
    )

    logger.info(
        "图片数量：%d",
        len(image_urls),
    )

    # --------------------------------------------------------
    # 下载这个文章的图片
    # --------------------------------------------------------

    article_id = get_article_id(
        url
    )

    local_images = download_images(
        image_urls,
        article_id,
    )

    # --------------------------------------------------------
    # 返回与你原始 JSON 完全一致的结构
    # --------------------------------------------------------

    return {
        "url": url,
        "title": title,
        "date": date,
        "content": content,
        "images": local_images,
    }


# ============================================================
# Add / replace ONLY target articles
# ============================================================

def add_articles() -> None:

    articles = load_articles()

    # --------------------------------------------------------
    # 建立 URL → index
    # --------------------------------------------------------

    existing_indexes = {}

    for index, article in enumerate(
        articles
    ):

        article_url = article.get(
            "url"
        )

        if article_url:

            existing_indexes[
                normalize_url(article_url)
            ] = index

    logger.info(
        "现有文章 URL：%d 个",
        len(existing_indexes),
    )

    added = 0
    replaced = 0
    failed = 0

    # --------------------------------------------------------
    # 只处理 TARGET_ARTICLES
    # --------------------------------------------------------

    for target_url in TARGET_ARTICLES:

        target_url = normalize_url(
            target_url
        )

        logger.info("")
        logger.info(
            "处理目标：%s",
            target_url,
        )

        try:

            article = crawl_article(
                target_url
            )

            # ------------------------------------------------
            # 如果 JSON 已经存在这个 URL
            #
            # 替换它，而不是产生重复文章。
            # ------------------------------------------------

            if target_url in existing_indexes:

                index = existing_indexes[
                    target_url
                ]

                logger.info(
                    "文章已经存在，将更新该条目。"
                )

                articles[index] = article

                replaced += 1

            # ------------------------------------------------
            # 不存在 → 追加
            # ------------------------------------------------

            else:

                logger.info(
                    "文章不存在，将追加。"
                )

                articles.append(
                    article
                )

                existing_indexes[
                    target_url
                ] = len(articles) - 1

                added += 1

        except Exception as exc:

            failed += 1

            logger.exception(
                "处理失败：%s",
                target_url,
            )

    # ========================================================
    # 重新序列化
    # ========================================================

    # 这里会重新保存整个 JSON，
    # 但不会重新抓取其它文章。
    #
    # 只有上面两个 TARGET_ARTICLES 会发生网络请求。

    if added > 0 or replaced > 0:

        # 按日期倒序
        articles.sort(
            key=lambda item: item.get(
                "date",
                "",
            ),
            reverse=True,
        )

        ARTICLES_FILE.write_text(
            json.dumps(
                articles,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        logger.info(
            "articles.json 已重新序列化。"
        )

    else:

        logger.info(
            "没有成功添加或更新文章，"
            "不修改 articles.json。"
        )

    # ========================================================
    # Summary
    # ========================================================

    logger.info("")
    logger.info("=" * 70)
    logger.info("补充任务完成")
    logger.info("=" * 70)

    logger.info(
        "新增：%d",
        added,
    )

    logger.info(
        "更新：%d",
        replaced,
    )

    logger.info(
        "失败：%d",
        failed,
    )

    logger.info(
        "最终文章数量：%d",
        len(articles),
    )

    logger.info("=" * 70)


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    add_articles()
