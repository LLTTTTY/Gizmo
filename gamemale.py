import logging
import requests
import re
import ddddocr
import os
import time
from urllib.parse import urljoin
from bs4 import BeautifulSoup

def setup_logger(name, verbose=False):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    
    if logger.handlers:
        logger.handlers.clear()
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)-10s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    return logger

class Gamemale:
    def __init__(self, username, password, questionid='0', answer=None, verbose=True):
        self.verbose = verbose
        self.main_logger = setup_logger('GameMale', verbose)
        self.login_logger = setup_logger('登录', verbose)
        self.sign_logger = setup_logger('签到', verbose)
        self.exchange_logger = setup_logger('抽奖', verbose)
        self.shock_logger = setup_logger('震惊', verbose)
        
        self.login_logger.debug(f"当前用户: {username}")
        
        self.ocr = ddddocr.DdddOcr(show_ad=False)
        self.post_formhash = None
        self.sign_result = None
        self.exchange_result = None
        self.shock_result = None
        self.username = str(username)
        self.password = str(password)
        self.questionid = questionid
        self.answer = str(answer) if answer else ""
        self.hostname = "www.gamemale.com"
        self.session = requests.session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Referer': f"https://{self.hostname}/",
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive'
        })

    def get_login_formhash(self):
        url = f"https://{self.hostname}/member.php?mod=logging&action=login"
        self.login_logger.debug(f"登录页url: {url}")
        text = self.session.get(url).text
        loginhash_match = re.search(r'<div id="main_message_(.+?)">', text)
        formhash_match = re.search(
            r'<input type="hidden" name="formhash" value="(.+?)" />',
            text
        )
        if not loginhash_match or not formhash_match:
            self.login_logger.debug(f"登录页:\n{text[:500]}...")
            raise ValueError("无法获取 loginhash 或 formhash")
        loginhash = loginhash_match.group(1)
        formhash = formhash_match.group(1)
        self.login_logger.debug(f"已成功获取登录所需的 loginhash:'{loginhash}'，formhash:'{formhash}'")
        return loginhash, formhash

    def verify_code(self, max_retries=10) -> str:
        self.login_logger.info(f"开始识别验证码 [最多重试 {max_retries} 次]")
        
        for attempt in range(1, max_retries + 1):
            update_url = (
                f"https://{self.hostname}/misc.php?mod=seccode&action=update"
                f"&idhash=cSA&{time.time()}&modid=member::logging"
            )
            self.login_logger.debug(f"获取验证码参数: {update_url}")
            update_text = self.session.get(update_url).text
            update_match = re.search(r"update=(.+?)&idhash=", update_text)
            if not update_match:
                self.login_logger.debug(f"验证码参数响应: {update_text}")
                continue
            update_val = update_match.group(1)
            code_url = (
                f"https://{self.hostname}/misc.php?mod=seccode&update="
                f"{update_val}&idhash=cSA"
            )
            self.login_logger.debug(f"获取验证码图片: {code_url}")
            headers = {
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Referer': f"https://{self.hostname}/member.php?mod=logging&action=login",
            }
            code_resp = self.session.get(code_url, headers=headers)
            if not code_resp.content:
                self.login_logger.debug(f"验证码图片为空")
                continue
                
            code = self.ocr.classification(code_resp.content)
            
            verify_url = (
                f"https://{self.hostname}/misc.php?mod=seccode&action=check&inajax=1&"
                f"modid=member::logging&idhash=cSA&secverify={code}"
            )
            self.login_logger.debug(f"验证验证码: {verify_url}")
            res = self.session.get(verify_url).text
            if "succeed" in res:
                self.login_logger.info(f"验证码识别成功: {code} (第{attempt}次)")
                return code
            else:
                self.login_logger.warning(f"验证码识别失败: {code} (第{attempt}次)")
        self.login_logger.error("超出最大重试次数，验证码识别失败")
        return ""

    def login(self) -> bool:
        self.login_logger.info(f"开始登录")
        
        code = self.verify_code()
        if not code:
            self.login_logger.error("缺少验证码，无法执行登录流程")
            return False
        loginhash, formhash = self.get_login_formhash()
        login_url = (
            f"https://{self.hostname}/member.php?mod=logging&action=login"
            f"&loginsubmit=yes&loginhash={loginhash}&inajax=1"
        )
        form_data = {
            'formhash': formhash,
            'referer': f"https://{self.hostname}/",
            'loginfield': 'username',
            'username': self.username,
            'password': self.password,
            'questionid': self.questionid,
            'answer': self.answer,
            'cookietime': 2592000,
            'seccodehash': 'cSA',
            'seccodemodid': 'member::logging',
            'seccodeverify': code,
        }
        
        self.login_logger.debug(f"提交登录表单: {login_url}")
        resp_text = self.session.post(login_url, data=form_data).text
        if "succeed" in resp_text:
            self.login_logger.info("登录成功")
            
            # 验证登录状态
            test_url = f"https://{self.hostname}/home.php?mod=space"
            test_resp = self.session.get(test_url)
            if self.username in test_resp.text:
                self.login_logger.debug("验证登录状态：已成功登录")
            else:
                self.login_logger.warning("登录响应显示成功，但实际未登录")
                return False
            
            self.login_logger.debug(f"获取签到所需的 formhash")
            forum_url = f"https://{self.hostname}/forum.php"
            try:
                text = self.session.get(forum_url).text
                formhash_match = re.search(
                    r'<input type="hidden" name="formhash" value="(.+?)" />',
                    text
                )
                if formhash_match:
                    self.post_formhash = formhash_match.group(1)
                    self.login_logger.debug(f"formhash:'{self.post_formhash}'")
                else:
                    self.login_logger.warning("无法获取 formhash")
            except Exception as e:
                self.login_logger.error(f"访问论坛主页出错: {e}")
                
            return True
        else:
            self.login_logger.error("登录失败")
            self.login_logger.debug(f"登录响应:\n{resp_text[:500]}...")
            return False

    def sign_gamemale(self):
        self.sign_logger.info("正在签到")
        if not self.post_formhash:
            self.sign_logger.warning("缺少 formhash ，无法执行签到流程")
            return
        sign_url = (
            f"https://{self.hostname}/k_misign-sign.html?"
            f"operation=qiandao&format=button&formhash={self.post_formhash}"
        )
        try:
            self.sign_logger.debug(f"发送签到请求: {sign_url}")
            resp = self.session.get(sign_url)
            response_text = resp.text
            if response_text.startswith("<?xml"):
                cdata_start = response_text.find("<![CDATA[") + 9
                cdata_end = response_text.find("]]>")
                if cdata_start > 8 and cdata_end > cdata_start:
                    message = response_text[cdata_start:cdata_end]
                else:
                    message = response_text
            else:
                message = response_text
            self.sign_logger.debug(f"签到响应: {message}")
            if "签到成功" in message:
                sign_status = "签到成功"
            elif "已签" in message:
                sign_status = "今日已签"
            else:
                sign_status = f"未知状态: {message[:100]}"
            self.sign_result = {
                "site": "GameMale",
                "status": sign_status
            }
            self.sign_logger.info(f"签到结果: {sign_status}")
        except Exception as e:
            self.sign_logger.error(f"签到失败: {e}")
            self.sign_result = {
                "site": "GameMale",
                "status": f"签到请求失败: {str(e)}"
            }

    def daily_exchange(self):
        self.exchange_logger.info("正在参与卡片抽奖")
        if not self.post_formhash:
            self.exchange_logger.warning("未能获取 formhash，无法进行日常卡片抽奖")
            return
            
        timestamp = str(int(time.time() * 1000))
        exchange_url = (
            f"https://{self.hostname}/plugin.php?id=it618_award:ajax&ac=getaward"
            f"&formhash={self.post_formhash}&_={timestamp}"
        )
        headers = {
            'accept': 'application/json, text/javascript, /; q=0.01',
            'referer': f"https://{self.hostname}/it618_award-award.html",
            'x-requested-with': 'XMLHttpRequest',
        }
        try:
            self.exchange_logger.debug(f"发送抽奖请求: {exchange_url}")
            response = self.session.get(exchange_url, headers=headers)
            res_json = response.json()
            self.exchange_logger.debug(f"抽奖响应: {res_json}")
            
            if res_json.get("tipname") == "":
                exchange_status = "今日已抽奖"
            elif res_json.get("tipname") == "ok":
                exchange_status = f"抽奖成功: {res_json.get('tipvalue')}"
            else:
                exchange_status = f"抽奖异常: {res_json}"
                
            self.exchange_result = {
                "site": "GameMale",
                "exchange_status": exchange_status
            }
            self.exchange_logger.info(f"抽奖结果: {exchange_status}")
        except Exception as e:
            self.exchange_logger.error(f"卡片抽奖失败: {e}")
            self.exchange_result = {
                "site": "GameMale",
                "exchange_status": f"抽奖请求失败: {str(e)}"
            }

    def shock_operation(self):
        """一键震惊（适配ajaxmenus表情菜单）"""
        self.shock_logger.info("开始执行一键震惊操作")
        shock_count = 0
        page = 1
        max_pages = 5
        target_count = 10

        while shock_count < target_count and page <= max_pages:
            try:
                # 访问博客列表页
                blog_url = (
                    f"https://{self.hostname}/home.php?mod=space&do=blog"
                    f"&view=all&catid=14&page={page}"
                )
                self.shock_logger.info(f"访问博客列表页 [{page}/{max_pages}]: {blog_url}")
                resp = self.session.get(blog_url, timeout=10)
                
                if resp.status_code != 200:
                    self.shock_logger.error(f"访问失败，状态码: {resp.status_code}")
                    page += 1
                    continue

                # 提取博客链接
                soup = BeautifulSoup(resp.text, 'html.parser')
                blog_links = []
                all_a_tags = soup.find_all('a', href=True)
                for a in all_a_tags:
                    if 'blog.php?tid=' in a['href'] and a['href'] not in blog_links:
                        blog_links.append(a['href'])
                
                blog_links = list(set(blog_links))
                self.shock_logger.info(f"第{page}页找到 {len(blog_links)} 个有效博客链接")
                
                if not blog_links:
                    self.shock_logger.warning(f"第{page}页未找到博客链接")
                    page += 1
                    continue

                # 遍历博客链接
                for link in blog_links:
                    if shock_count >= target_count:
                        break

                    try:
                        # 访问博客详情页
                        blog_detail_url = urljoin(f"https://{self.hostname}", link)
                        self.shock_logger.debug(f"访问博客: {blog_detail_url}")
                        blog_resp = self.session.get(blog_detail_url, timeout=10)
                        
                        if blog_resp.status_code != 200:
                            self.shock_logger.debug("博客访问失败，跳过")
                            continue

                        # 第一步：找到ajaxmenus表情菜单链接
                        menu_match = re.search(r'href="([^"]*ajaxmenus[^"]*type=attitude[^"]*)"', blog_resp.text)
                        if not menu_match:
                            self.shock_logger.debug("未找到表情菜单链接，跳过")
                            continue
                        
                        menu_url = urljoin(f"https://{self.hostname}", menu_match.group(1))
                        # 添加formhash参数
                        if self.post_formhash and 'formhash=' not in menu_url:
                            menu_url += f"&formhash={self.post_formhash}"
                        
                        self.shock_logger.debug(f"请求表情菜单: {menu_url}")
                        menu_resp = self.session.get(menu_url, timeout=10)

                        # 第二步：从菜单中找到"震惊"表情的提交链接
                        shock_match = re.search(r'href="([^"]*handlekey=shock[^"]*)"', menu_resp.text)
                        if not shock_match:
                            self.shock_logger.debug("未找到震惊表情链接，跳过")
                            continue
                        
                        shock_url = urljoin(f"https://{self.hostname}", shock_match.group(1))
                        # 添加formhash参数
                        if self.post_formhash and 'formhash=' not in shock_url:
                            shock_url += f"&formhash={self.post_formhash}"

                        # 第三步：执行震惊操作
                        self.shock_logger.debug(f"执行震惊操作: {shock_url}")
                        shock_resp = self.session.get(shock_url, timeout=10)

                        # 验证是否成功
                        if "succeed" in shock_resp.text or "messagetext" not in shock_resp.text:
                            shock_count += 1
                            self.shock_logger.info(f"成功震惊 {shock_count}/{target_count} 次")
                            # 延迟避免风控
                            time.sleep(1.5)
                        else:
                            self.shock_logger.debug("该博客已震惊过或操作失败")

                    except Exception as e:
                        self.shock_logger.warning(f"处理博客失败: {str(e)[:80]}")
                        continue

            except Exception as e:
                self.shock_logger.error(f"处理第{page}页失败: {str(e)}")
            
            page += 1

        # 设置最终结果
        if shock_count >= target_count:
            self.shock_result = {
                "site": "GameMale",
                "status": f"震惊完成，共成功 {shock_count} 次"
            }
        else:
            self.shock_result = {
                "site": "GameMale",
                "status": f"震惊未完成，仅成功 {shock_count} 次（目标{target_count}次）"
            }
        self.shock_logger.info(self.shock_result["status"])

    def run(self):
        self.main_logger.info("=== 全自动操作开始 ===")
        if not self.login():
            self.main_logger.error("登录失败，终止操作")
            return
        
        # 执行签到
        self.sign_gamemale()
        
        # 执行抽奖
        self.daily_exchange()
        
        # 执行一键震惊
        self.shock_operation()
        
        # 输出最终结果
        self.main_logger.info("=== 今日操作成果 ===")
        if self.sign_result:
            self.main_logger.info(f"签到: {self.sign_result['status']}")
        if self.exchange_result:
            self.main_logger.info(f"抽奖: {self.exchange_result['exchange_status']}")
        if self.shock_result:
            self.main_logger.info(f"震惊: {self.shock_result['status']}")

def main():
    # 从环境变量获取账号密码
    username = os.getenv("GAMEMALE_USERNAME") or os.getenv("USERNAME")
    password = os.getenv("GAMEMALE_PASSWORD") or os.getenv("PASSWORD")
    
    if not username or not password:
        logger = setup_logger("GameMale")
        logger.error("请先设置环境变量：")
        logger.error("Windows: set GAMEMALE_USERNAME=你的账号 && set GAMEMALE_PASSWORD=你的密码")
        logger.error("Linux/Mac: export GAMEMALE_USERNAME=你的账号 && export GAMEMALE_PASSWORD=你的密码")
        exit(1)
    
    # 初始化并运行
    gm = Gamemale(username, password, verbose=True)
    gm.run()

if __name__ == "__main__":
    main()
