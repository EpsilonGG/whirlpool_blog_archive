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
# 配置
# ============================================================

BASE_URL = "https://whirlpool.co.jp"
BLOG_URL = f"{BASE_URL}/release/blog/"

# 最终 JSON
OUTPUT_FILE = Path("articles.json")

# 图片保存目录
IMAGE_DIR = Path("images")

# 请求间隔，避免请求过快
REQUEST_DELAY = 0.5

# 请求超时时间
REQUEST_TIMEOUT = 30

# 最大分页数
# 当前网站数量不多，设置大一些即可。
MAX_PAGES = 100

# User-Agent
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)


# ============================================================
# 日志
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
        pool_connections=10,
        pool_maxsize=10,
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
        }
    )

    return session


session = create_session()


# ============================================================
# 基础工具
# ============================================================

def sleep_between_requests() -> None:
    time.sleep(REQUEST_DELAY)


def get(url: str) -> requests.Response:
    """
    统一 GET 请求。
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

    sleep_between_requests()

    return response


def get_soup(url: str) -> BeautifulSoup:
    response = get(url)

    return BeautifulSoup(
        response.content,
        "lxml",
    )


def clean_text(text: str) -> str:
    """
    清理正文文本。

    重点：
    - 保留换行
    - 删除行首行尾多余空白
    - 压缩连续空行
    """

    text = text.replace(
        "\r\n",
        "\n",
    )

    text = text.replace(
        "\r",
        "\n",
    )

    lines = []

    for line in text.split("\n"):
        line = line.strip()

        if line:
            lines.append(line)
        else:
            # 暂时保留空行
            lines.append("")

    text = "\n".join(lines)

    # 最多连续两个空行
    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


def safe_filename(name: str) -> str:
    """
    将文件名处理成 Windows/Linux 都能使用的形式。
    """

    name = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "_",
        name,
    )

    name = name.strip(
        " ."
    )

    if not name:
        name = "unknown"

    return name


# ============================================================
# 分页
# ============================================================

def get_page_url(page: int) -> str:
    """
    Whirlpool Blog 分页：

    第 1 页：
    /release/blog/

    第 2 页：
    /release/blog/page/2/

    ...
    """

    if page == 1:
        return BLOG_URL

    return f"{BLOG_URL}page/{page}/"


def is_valid_blog_page(
    soup: BeautifulSoup,
) -> bool:
    """
    判断页面是不是 Staff Blog 页面。
    """

    # 页面上应该存在：
    # スタッフ日誌

    text = soup.get_text(
        " ",
        strip=True,
    )

    return "スタッフ日誌" in text


def discover_pages() -> list[str]:
    """
    自动发现所有 Blog 分页。

    不直接假设只有 9 页。
    """

    pages = []

    failed_pages = 0

    for page_number in range(
        1,
        MAX_PAGES + 1,
    ):

        url = get_page_url(
            page_number
        )

        try:
            logger.info(
                "检查目录页：第 %d 页",
                page_number,
            )

            soup = get_soup(url)

            if not is_valid_blog_page(
                soup
            ):
                failed_pages += 1

                logger.warning(
                    "不是有效 Blog 页面：%s",
                    url,
                )

                # 连续两页失败就认为分页结束
                if failed_pages >= 2:
                    break

                continue

            pages.append(url)

            failed_pages = 0

        except Exception as exc:

            failed_pages += 1

            logger.warning(
                "目录页请求失败：%s | %s",
                url,
                exc,
            )

            if failed_pages >= 2:
                break

    logger.info(
        "共发现 %d 个 Blog 目录页",
        len(pages),
    )

    return pages


# ============================================================
# 文章 URL
# ============================================================

def is_article_url(
    url: str,
) -> bool:
    """
    判断是否为 Whirlpool Blog 单篇文章。

    例如：

    /release/blog/blog20260807/

    /release/blog/blog20170224/
    """

    parsed = urlparse(url)

    # 只允许 whirlpool.co.jp
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


def normalize_url(
    url: str,
) -> str:
    """
    统一 URL：

    - 去掉 query
    - 去掉 fragment
    - 统一尾部 /
    """

    parsed = urlparse(url)

    path = parsed.path.rstrip("/") + "/"

    return (
        f"{parsed.scheme}://"
        f"{parsed.netloc}"
        f"{path}"
    )


def discover_article_urls(
    page_url: str,
) -> list[str]:

    soup = get_soup(
        page_url
    )

    urls = []

    for a in soup.find_all(
        "a",
        href=True,
    ):

        href = a.get("href")

        if not href:
            continue

        url = urljoin(
            page_url,
            href,
        )

        url = normalize_url(
            url
        )

        if is_article_url(url):
            urls.append(url)

    # 去重，同时保持顺序
    return list(
        dict.fromkeys(urls)
    )


def discover_all_article_urls() -> list[str]:
    """
    扫描所有目录页。
    """

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
# 文章 ID
# ============================================================

def get_article_id(
    url: str,
) -> str:

    match = re.search(
        r"/(blog\d{8})/?$",
        url,
    )

    if match:
        return match.group(1)

    return safe_filename(
        url.rstrip("/").split("/")[-1]
    )


# ============================================================
# 文章解析
# ============================================================

def extract_title(
    soup: BeautifulSoup,
) -> str:
    """
    标题：

    <article class="common-container">
        <h4>...</h4>
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
    h4 = soup.find("h4")

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
    时间：

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
    正文：

    <div class="main-text">
        ...
    </div>
    """

    node = soup.select_one(
        "div.main-text"
    )

    if not node:
        return ""

    # 使用 get_text("\n")：
    # HTML 中的 <br>、段落、块元素会尽量转换成换行。
    text = node.get_text(
        "\n",
        strip=False,
    )

    return clean_text(
        text
    )


# ============================================================
# 图片
# ============================================================

def extract_image_urls(
    soup: BeautifulSoup,
    article_url: str,
) -> list[str]:
    """
    图片结构：

    <div class="image-box">
        <img src="...">
    </div>

    返回图片 URL。
    """

    image_urls = []

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

            # 去掉 query / fragment
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
# 图片下载
# ============================================================

def get_image_extension(
    image_url: str,
    response: requests.Response,
) -> str:

    # 首选 URL 后缀
    path = urlparse(
        image_url
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

    # fallback：Content-Type
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
    """
    下载文章图片。

    保存：

    images/
        blog20260807/
            001.png
            002.jpg

    返回：

    [
        "images/blog20260807/001.png",
        "images/blog20260807/002.jpg"
    ]
    """

    if not image_urls:
        return []

    article_image_dir = (
        IMAGE_DIR
        / article_id
    )

    article_image_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    local_paths = []

    for index, image_url in enumerate(
        image_urls,
        start=1,
    ):

        try:

            logger.info(
                "    下载图片 [%d/%d] %s",
                index,
                len(image_urls),
                image_url,
            )

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

            # 防止错误页面被保存成图片
            if (
                content_type
                and not content_type.startswith(
                    "image/"
                )
            ):

                logger.warning(
                    "    跳过非图片资源：%s",
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

            file_path = (
                article_image_dir
                / filename
            )

            file_path.write_bytes(
                response.content
            )

            # JSON 使用正斜杠
            relative_path = (
                file_path
                .as_posix()
            )

            local_paths.append(
                relative_path
            )

        except Exception as exc:

            logger.warning(
                "    图片下载失败：%s | %s",
                image_url,
                exc,
            )

    return local_paths


# ============================================================
# 单篇文章
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
        "    正文：%d 字",
        len(content),
    )

    logger.info(
        "    图片：%d 张",
        len(image_urls),
    )

    # 下载图片
    images = download_images(
        image_urls,
        article_id,
    )

    return {
        "url": url,
        "title": title,
        "date": date,
        "content": content,
        "images": images,
    }


# ============================================================
# JSON
# ============================================================

def load_existing_articles() -> list[dict]:
    """
    如果已经存在 articles.json，
    则读取之前已经成功抓取的数据。
    """

    if not OUTPUT_FILE.exists():
        return []

    try:

        data = json.loads(
            OUTPUT_FILE.read_text(
                encoding="utf-8"
            )
        )

        if isinstance(data, list):
            return data

        return []

    except Exception as exc:

        logger.warning(
            "读取已有 JSON 失败：%s",
            exc,
        )

        return []


def save_articles(
    articles: list[dict],
) -> None:
    """
    保存 JSON。

    按日期倒序。
    """

    def sort_key(
        item: dict,
    ) -> str:

        return item.get(
            "date",
            "",
        )

    articles.sort(
        key=sort_key,
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
# 主程序
# ============================================================

def main():

    logger.info("=" * 70)
    logger.info(
        "Whirlpool Staff Blog Crawler"
    )
    logger.info("=" * 70)

    IMAGE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # 1. 扫描所有文章 URL
    # --------------------------------------------------------

    article_urls = (
        discover_all_article_urls()
    )

    if not article_urls:

        logger.error(
            "没有发现任何文章。"
        )

        return

    # --------------------------------------------------------
    # 2. 读取已有数据
    # --------------------------------------------------------

    articles = (
        load_existing_articles()
    )

    existing_urls = {
        article.get("url")
        for article in articles
        if article.get("url")
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
            "================================================"
        )

        logger.info(
            "[%d/%d]",
            index,
            total,
        )

        # ----------------------------------------------------
        # 已经抓过
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
                if item.get("url") != url
            ]

            articles.append(
                article
            )

            # 每完成一篇立即保存
            # 防止程序中断造成大量进度丢失
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

    logger.info("")
    logger.info("=" * 70)
    logger.info(
        "全部任务完成"
    )
    logger.info("=" * 70)

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
        "图片：%s",
        IMAGE_DIR,
    )

    logger.info("=" * 70)


if __name__ == "__main__":
    main()
