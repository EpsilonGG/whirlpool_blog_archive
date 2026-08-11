import json
import os
import re
import time
from collections import defaultdict
from urllib.parse import urlparse, parse_qs, unquote

import requests
from tqdm import tqdm


# ============================================================
# 配置
# ============================================================

JSON_FILE = "articles.json"
IMAGE_DIR = "images"

REQUEST_TIMEOUT = 30

# 图片之间的间隔
IMAGE_DELAY = 0.5

# 失败重试次数
MAX_RETRIES = 3


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "image/avif,image/webp,image/apng,"
        "image/svg+xml,image/*,*/*;q=0.8"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://wpblog.exblog.jp/",
}


session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# URL转换
# ============================================================

def convert_to_real_image_url(url):
    """
    将 Exblog 的图片详情URL转换为真实图片URL。

    例如：

    输入：

    https://wpblog.exblog.jp/iv/detail/
        ?s=11164089
        &i=200906%2F04%2F74%2Fb0106674_041152.jpg

    输出：

    https://pds.exblog.jp/pds/1/200906/04/74/b0106674_041152.jpg
    """

    if not url:
        return None


    parsed = urlparse(url)


    # --------------------------------------------------------
    # 已经是真实 pds 图片URL
    # --------------------------------------------------------

    if parsed.netloc.lower() == "pds.exblog.jp":

        return url


    # --------------------------------------------------------
    # Exblog /iv/detail/
    # --------------------------------------------------------

    if (
        parsed.netloc.lower()
        == "wpblog.exblog.jp"
        and
        parsed.path.startswith("/iv/detail")
    ):

        query = parse_qs(
            parsed.query
        )


        values = query.get("i")


        if not values:

            print(
                f"无法从图片详情URL找到 i 参数：\n{url}"
            )

            return None


        image_path = values[0]


        # URL decode
        image_path = unquote(
            image_path
        )


        # 去掉开头 /
        image_path = image_path.lstrip("/")


        # ----------------------------------------------------
        # 防止已经包含 pds/1/
        # ----------------------------------------------------

        if image_path.startswith(
            "pds/1/"
        ):

            image_path = image_path[
                len("pds/1/"):
            ]


        # ----------------------------------------------------
        # 正常情况：
        #
        # 200906/04/74/file.jpg
        #
        # ↓
        #
        # pds/1/200906/04/74/file.jpg
        # ----------------------------------------------------

        real_url = (
            "https://pds.exblog.jp/pds/1/"
            + image_path
        )


        return real_url


    # --------------------------------------------------------
    # 如果是普通URL，但不是Exblog detail
    #
    # 作为最后的fallback直接返回
    # --------------------------------------------------------

    return url


# ============================================================
# 判断是否是真正的图片
# ============================================================

def get_image_extension(data, content_type=""):
    """
    根据文件头判断图片类型。

    不相信URL后缀。
    """

    # JPEG
    if data.startswith(
        b"\xff\xd8\xff"
    ):
        return ".jpg"


    # PNG
    if data.startswith(
        b"\x89PNG\r\n\x1a\n"
    ):
        return ".png"


    # GIF
    if data.startswith(
        b"GIF87a"
    ) or data.startswith(
        b"GIF89a"
    ):
        return ".gif"


    # WEBP
    if (
        len(data) >= 12
        and
        data[0:4] == b"RIFF"
        and
        data[8:12] == b"WEBP"
    ):
        return ".webp"


    # BMP
    if data.startswith(
        b"BM"
    ):
        return ".bmp"


    # Content-Type fallback
    content_type = content_type.lower()


    if "jpeg" in content_type:
        return ".jpg"

    if "png" in content_type:
        return ".png"

    if "gif" in content_type:
        return ".gif"

    if "webp" in content_type:
        return ".webp"

    if "bmp" in content_type:
        return ".bmp"


    return None


# ============================================================
# 下载图片
# ============================================================

def download_image(
    image_url,
    filename_without_extension
):
    """
    下载图片。

    重要：
    如果服务器返回HTML而不是图片，
    不会把HTML保存成.jpg。
    """

    last_error = None


    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        try:

            response = session.get(
                image_url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True
            )


            # ------------------------------------------------
            # HTTP状态
            # ------------------------------------------------

            if response.status_code != 200:

                raise RuntimeError(
                    f"HTTP {response.status_code}"
                )


            data = response.content


            # ------------------------------------------------
            # 判断是不是图片
            # ------------------------------------------------

            ext = get_image_extension(
                data,
                response.headers.get(
                    "Content-Type",
                    ""
                )
            )


            if not ext:

                # 看看是不是HTML
                sample = data[:500].lower()


                if (
                    b"<html" in sample
                    or
                    b"<!doctype" in sample
                    or
                    b"<body" in sample
                ):

                    raise RuntimeError(
                        "服务器返回的是HTML页面，不是图片"
                    )


                raise RuntimeError(
                    "返回内容无法识别为图片"
                )


            # ------------------------------------------------
            # 保存
            # ------------------------------------------------

            path = os.path.join(
                IMAGE_DIR,
                filename_without_extension
                + ext
            )


            with open(
                path,
                "wb"
            ) as f:

                f.write(data)


            return os.path.basename(
                path
            )


        except Exception as e:

            last_error = e


            if attempt < MAX_RETRIES:

                time.sleep(
                    2 * attempt
                )


    print(
        f"\n图片下载失败："
        f"{image_url}"
    )

    print(
        f"错误：{last_error}"
    )


    return None


# ============================================================
# 日期转换
# ============================================================

def date_to_key(date_text):
    """
    2009年07月23日
    ↓
    20090723
    """

    if not date_text:

        return "unknown"


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
# 主程序
# ============================================================

def main():

    # --------------------------------------------------------
    # 创建图片目录
    # --------------------------------------------------------

    os.makedirs(
        IMAGE_DIR,
        exist_ok=True
    )


    # --------------------------------------------------------
    # 检查JSON
    # --------------------------------------------------------

    if not os.path.exists(
        JSON_FILE
    ):

        raise FileNotFoundError(
            f"找不到 {JSON_FILE}\n"
            f"请确认 articles.json 位于仓库根目录。"
        )


    # --------------------------------------------------------
    # 读取JSON
    # --------------------------------------------------------

    with open(
        JSON_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        articles = json.load(f)


    print(
        f"文章数量：{len(articles)}"
    )


    # --------------------------------------------------------
    # 日期计数器
    #
    # 例如：
    #
    # 20090604-01.jpg
    # 20090604-02.jpg
    # 20090604-03.jpg
    #
    # 同一天不同文章也会继续编号。
    # --------------------------------------------------------

    date_counters = defaultdict(
        int
    )


    # --------------------------------------------------------
    # 如果images目录里已经有文件，
    # 先扫描已有编号，避免覆盖。
    # --------------------------------------------------------

    pattern = re.compile(
        r"^(\d{8})-(\d{2})\.[^.]+$"
    )


    for filename in os.listdir(
        IMAGE_DIR
    ):

        match = pattern.match(
            filename
        )

        if not match:

            continue


        date_key = match.group(1)

        number = int(
            match.group(2)
        )


        date_counters[
            date_key
        ] = max(
            date_counters[date_key],
            number
        )


    # --------------------------------------------------------
    # 统计总图片数
    # --------------------------------------------------------

    total_images = 0


    for article in articles:

        images = article.get(
            "images",
            []
        )

        total_images += len(
            images
        )


    print(
        f"预计图片数量：{total_images}"
    )


    # --------------------------------------------------------
    # 开始处理
    # --------------------------------------------------------

    downloaded = 0

    failed = 0


    for article in tqdm(
        articles,
        desc="处理文章"
    ):

        date = article.get(
            "date",
            ""
        )


        date_key = date_to_key(
            date
        )


        images = article.get(
            "images",
            []
        )


        for image in images:

            old_url = image.get(
                "url"
            )


            if not old_url:

                failed += 1

                continue


            # ------------------------------------------------
            # 转换真实URL
            # ------------------------------------------------

            real_url = (
                convert_to_real_image_url(
                    old_url
                )
            )


            if not real_url:

                failed += 1

                continue


            # ------------------------------------------------
            # 更新JSON中的URL
            #
            # 以后articles.json直接保存真实图片URL。
            # ------------------------------------------------

            image["url"] = real_url


            # ------------------------------------------------
            # 日期编号
            # ------------------------------------------------

            date_counters[
                date_key
            ] += 1


            number = date_counters[
                date_key
            ]


            filename = (
                f"{date_key}-"
                f"{number:02d}"
            )


            # ------------------------------------------------
            # 下载
            # ------------------------------------------------

            saved = download_image(
                real_url,
                filename
            )


            if saved:

                image["file"] = saved

                downloaded += 1

            else:

                failed += 1


            time.sleep(
                IMAGE_DELAY
            )


    # --------------------------------------------------------
    # 保存更新后的JSON
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # 结果
    # --------------------------------------------------------

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
        f"文章数量：{len(articles)}"
    )

    print(
        f"图片总数：{total_images}"
    )

    print(
        f"下载成功：{downloaded}"
    )

    print(
        f"下载失败：{failed}"
    )

    print(
        f"图片目录：{IMAGE_DIR}/"
    )

    print(
        f"JSON：{JSON_FILE}"
    )


if __name__ == "__main__":

    main()
