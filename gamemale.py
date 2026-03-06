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
                'Chrome/122.0.0.0 Safari/537.36'
            ),
            'Referer': f"https://{self.hostname}/",
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
        # 禁用重定向跟踪，便于调试
        self.session.max_redirects = 10

    def get_login_formhash(self):
        """修复版：多规则匹配loginhash和formhash"""
        url = f"https://{self.hostname}/member.php?mod=logging&action=login"
        self.login_logger.debug(f"登录页url: {url}")
        
        # 多次尝试获取登录页
        for attempt in range(3):
            try:
                resp = self.session.get(url, timeout=15)
                resp.encoding = 'utf-8'
                text = resp.text
                self.login_logger.debug(f"登录页响应状态码: {resp.status_code}")
                
                # 保存登录页源码（调试用）
                with open("login_page.html", "w", encoding="utf-8") as f:
                    f.write(text[:2000])  # 只保存前2000字符
                
                # ===== 修复：多规则匹配loginhash =====
                loginhash = None
                # 规则1: 原规则（修复拼写错误）
                match1 = re.search(r'<div id="main_message_(.+?)">', text)
                # 规则2: 匹配loginhash的其他位置
                match2 = re.search(r'loginhash=([a-f0-9]+)', text)
                # 规则3: 从表单action中提取
                match3 = re.search(r'action="[^"]*loginhash=([a-f0-9]+)"', text)
                
                if match1:
                    loginhash = match1.group(1)
                elif match2:
                    loginhash = match2.group(1)
                elif match3:
                    loginhash = match3.group(1)
                
                # ===== 修复：多规则匹配formhash =====
                formhash = None
                # 规则1: 原规则
                match1 = re.search(r'<input type="hidden" name="formhash" value="(.+?)" />', text)
                # 规则2: 匹配所有formhash输入框（忽略空格）
                match2 = re.search(r'<input\s+name="formhash"\s+type="hidden"\s+value="([^"]+)"', text, re.IGNORECASE)
                # 规则3: 使用BeautifulSoup查找
                if not match1 and not match2:
                    soup = BeautifulSoup(text, 'html.parser')
                    formhash_input = soup.find('input', {'name': 'formhash', 'type': 'hidden'})
                    if formhash_input:
                        formhash = formhash_input.get('value')
                
                if match1:
                    formhash = match1.group(1)
                elif match2:
                    formhash = match2.group(1)
                
                # 验证是否获取成功
                if loginhash and formhash:
                    self.login_logger.debug(f"成功获取 - loginhash:'{loginhash}'，formhash:'{formhash}'")
                    return loginhash, formhash
                else:
                    self.login_logger.warning(f"第{attempt+1}次尝试 - loginhash: {loginhash}, formhash: {formhash}")
                    time.sleep(1)
                    
            except Exception as e:
                self.login_logger.error(f"获取登录参数失败（第{attempt+1}次）: {e}")
                time.sleep(1)
        
        # 如果都失败，尝试从主页获取formhash
        self.login_logger.debug("尝试从主页获取formhash...")
        try:
            index_resp = self.session.get(f"https://{self.hostname}/", timeout=10)
            soup = BeautifulSoup(index_resp.text, 'html.parser')
            formhash_input = soup.find('input', {'name': 'formhash', 'type': 'hidden'})
            if formhash_input:
                formhash = formhash_input.get('value')
                # 生成一个默认的loginhash（部分网站可通用）
                loginhash = 'a1b2c3d4'  # 占位值
                self.login_logger.warning(f"使用备用方案 - loginhash:'{loginhash}'，formhash:'{formhash}'")
                return loginhash, formhash
        except:
            pass
        
        self.login_logger.error("所有获取方式都失败！")
        raise ValueError("无法获取 loginhash 或 formhash")

    def verify_code(self, max_retries=10) -> str:
        self.login_logger.info(f"开始识别验证码 [最多重试 {max_retries} 次]")
        
        for attempt in range(1, max_retries + 1):
            try:
                update_url = (
                    f"https://{self.hostname}/misc.php?mod=seccode&action=update"
                    f"&idhash=cSA&{time.time()}&modid=member::logging"
                )
                self.login_logger.debug(f"获取验证码参数: {update_url}")
                update_text = self.session.get(update_url, timeout=10).text
                update_match = re.search(r"update=(.+?)&idhash=", update_text)
                if not update_match:
                    self.login_logger.debug(f"验证码参数响应: {update_text[:100]}")
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
                code_resp = self.session.get(code_url, headers=headers, timeout=10)
                if not code_resp.content:
                    self.login_logger.debug(f"验证码图片为空")
                    continue
                    
                code = self.ocr.classification(code_resp.content)
                
                verify_url = (
                    f"https://{self.hostname}/misc.php?mod=seccode&action=check&inajax=1&"
                    f"modid=member::logging&idhash=cSA&secverify={code}"
                )
                self.login_logger.debug(f"验证验证码: {verify_url}")
                res = self.session.get(verify_url, timeout=10).text
                if "succeed" in res:
                    self.login_logger.info(f"验证码识别成功: {code} (第{attempt}次)")
                    return code
                else:
                    self.login_logger.warning(f"验证码识别失败: {code} (第{attempt}次)")
            except Exception as e:
                self.login_logger.error(f"验证码处理出错: {e}")
                continue
                
        self.login_logger.error("超出最大重试次数，验证码识别失败")
        return ""

    def login(self) -> bool:
        self.login_logger.info(f"开始登录流程")
        
        # 先获取验证码（避免获取formhash后验证码过期）
        code = self.verify_code()
        if not code:
            self.login_logger.error("缺少验证码，无法执行登录流程")
            return False
        
        # 获取登录参数
        try:
            loginhash, formhash = self.get_login_formhash()
        except Exception as e:
            self.login_logger.error(f"获取登录参数失败: {e}")
            return False
        
        # 构造登录请求
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
        
        self.login_logger.debug(f"提交登录表单到: {login_url}")
        try:
            # 关闭重定向，手动处理
            resp = self.session.post(login_url, data=form_data, timeout=15, allow_redirects=False)
            resp.encoding = 'utf-8'
            resp_text = resp.text
            self.login_logger.debug(f"登录响应状态码: {resp.status_code}")
            self.login_logger.debug(f"登录响应内容: {resp_text[:300]}")
            
            # 多条件判断登录成功
            success_conditions = [
                "succeed" in resp_text,
                "登录成功" in resp_text,
                resp.status_code == 302  # 重定向表示成功
            ]
            
            if any(success_conditions):
                self.login_logger.info("登录成功！")
                
                # 验证登录状态
                try:
                    test_url = f"https://{self.hostname}/home.php?mod=space"
                    test_resp = self.session.get(test_url, timeout=10)
                    if self.username in test_resp.text:
                        self.login_logger.debug("登录状态验证通过")
                    else:
                        self.login_logger.warning("登录响应显示成功，但个人中心未找到用户名")
                except Exception as e:
                    self.login_logger.error(f"验证登录状态出错: {e}")
                
                # 获取formhash（用于后续操作）
                try:
                    forum_url = f"https://{self.hostname}/forum.php"
                    text = self.session.get(forum_url, timeout=10).text
                    soup = BeautifulSoup(text, 'html.parser')
                    formhash_input = soup.find('input', {'name': 'formhash', 'type': 'hidden'})
                    if formhash_input:
                        self.post_formhash = formhash_input.get('value')
                        self.login_logger.debug(f"获取到formhash: {self.post_formhash}")
                    else:
                        self.login_logger.warning("无法获取formhash，后续操作可能失败")
                except Exception as e:
                    self.login_logger.error(f"获取formhash出错: {e}")
                    
                return True
            else:
                self.login_logger.error("登录失败！")
                self.login_logger.debug(f"登录失败响应: {resp_text}")
                return False
                
        except Exception as e:
            self.login_logger.error(f"登录请求出错: {e}")
            return False

    def sign_gamemale(self):
        self.sign_logger.info("正在执行签到操作")
        if not self.post_formhash:
            self.sign_logger.warning("缺少 formhash ，无法执行签到流程")
            self.sign_result = {"site": "GameMale", "status": "缺少formhash，签到失败"}
            return
            
        sign_url = (
            f"https://{self.hostname}/k_misign-sign.html?"
            f"operation=qiandao&format=button&formhash={self.post_formhash}"
        )
        try:
            self.sign_logger.debug(f"签到请求URL: {sign_url}")
            resp = self.session.get(sign_url, timeout=10)
            resp.encoding = 'utf-8'
            response_text = resp.text
            
            # 解析签到响应
            message = response_text
            if response_text.startswith("<?xml"):
                cdata_start = response_text.find("<![CDATA[") + 9
                cdata_end = response_text.find("]]>")
                if cdata_start > 8 and cdata_end > cdata_start:
                    message = response_text[cdata_start:cdata_end]
            
            self.sign_logger.debug(f"签到响应: {message[:200]}")
            
            if "签到成功" in message:
                sign_status = "签到成功"
            elif "已签" in message:
                sign_status = "今日已签到"
            elif "需要先登录" in message:
                sign_status = "登录状态失效"
            else:
                sign_status = f"签到结果未知: {message[:100]}"
                
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
        self.exchange_logger.info("正在执行卡片抽奖操作")
        if not self.post_formhash:
            self.exchange_logger.warning("未能获取 formhash，无法进行日常卡片抽奖")
            self.exchange_result = {"site": "GameMale", "exchange_status": "缺少formhash，抽奖失败"}
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
            self.exchange_logger.debug(f"抽奖请求URL: {exchange_url}")
            response = self.session.get(exchange_url, headers=headers, timeout=10)
            res_json = response.json()
            self.exchange_logger.debug(f"抽奖响应: {res_json}")
            
            if res_json.get("tipname") == "":
                exchange_status = "今日已抽奖"
            elif res_json.get("tipname") == "ok":
                exchange_status = f"抽奖成功: {res_json.get('tipvalue')}"
            else:
                exchange_status = f"抽奖异常: {str(res_json)[:100]}"
                
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
        if not self.post_formhash:
            self.shock_logger.warning("缺少formhash，震惊操作无法执行")
            self.shock_result = {"site": "GameMale", "status": "缺少formhash，震惊失败"}
            return
            
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
                    href = a.get('href', '')
                    if 'blog.php?tid=' in href and href not in blog_links:
                        blog_links.append(href)
                
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
                        if 'formhash=' not in menu_url:
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
                        if 'formhash=' not in shock_url:
                            shock_url += f"&formhash={self.post_formhash}"

                        # 第三步：执行震惊操作
                        self.shock_logger.debug(f"执行震惊操作: {shock_url}")
                        shock_resp = self.session.get(shock_url, timeout=10)

                        # 验证是否成功
                        shock_text = shock_resp.text
                        if "succeed" in shock_text or "操作成功" in shock_text or "messagetext" not in shock_text:
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
        self.main_logger.info("=== GameMale 全自动操作开始 ===")
        
        # 登录
        login_success = self.login()
        if not login_success:
            self.main_logger.error("登录失败，终止所有操作")
            return
        
        # 执行签到
        self.sign_gamemale()
        
        # 执行抽奖
        self.daily_exchange()
        
        # 执行一键震惊
        self.shock_operation()
        
        # 输出最终结果
        self.main_logger.info("\n=== 今日操作成果汇总 ===")
        if self.sign_result:
            self.main_logger.info(f"📝 签到: {self.sign_result['status']}")
        if self.exchange_result:
            self.main_logger.info(f"🎁 抽奖: {self.exchange_result['exchange_status']}")
        if self.shock_result:
            self.main_logger.info(f"😲 震惊: {self.shock_result['status']}")
        self.main_logger.info("=== 操作完成 ===")

def main():
    # 从环境变量获取账号密码
    username = os.getenv("GAMEMALE_USERNAME") or os.getenv("USERNAME")
    password = os.getenv("GAMEMALE_PASSWORD") or os.getenv("PASSWORD")
    
    if not username or not password:
        logger = setup_logger("GameMale")
        logger.error("❌ 账号密码未设置！")
        logger.error("请先设置环境变量：")
        logger.error("  Windows:")
        logger.error("    set GAMEMALE_USERNAME=你的账号")
        logger.error("    set GAMEMALE_PASSWORD=你的密码")
        logger.error("  Linux/Mac:")
        logger.error("    export GAMEMALE_USERNAME=你的账号")
        logger.error("    export GAMEMALE_PASSWORD=你的密码")
        exit(1)
    
    # 初始化并运行
    try:
        gm = Gamemale(username, password, verbose=True)
        gm.run()
    except Exception as e:
        logger = setup_logger("GameMale")
        logger.error(f"程序运行出错: {e}")
        exit(1)

if __name__ == "__main__":
    main()
