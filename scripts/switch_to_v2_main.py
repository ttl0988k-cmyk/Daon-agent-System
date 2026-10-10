# -*- coding: utf-8 -*-
"""
DAON Main UI를 v2로 공식 승격(Switch) 및 구버전 파일 안전 백업 스크립트.
- 모나코 에디터는 대표님 요청에 따라 손대지 않고 100% 안전 보존
- 구버전 index.html을 _backup_old_ui/ 로 백업
- 루트 index.html을 최신 static/v2/index.html로 교체 (개발 소스 + resources 배포 폴더 양쪽 모두)
- 이제 http://127.0.0.1:9090/ 접속 시 즉시 최신 v2 화면과 브라우저 에이전트 뷰어가 첫 화면으로 뜸
"""
import os
import shutil

ROOT_DIR = r"c:\daon\Daon agent System"
DEPLOY_DIR = r"C:\Users\ttl09\AppData\Local\Programs\daon-agent-system\resources"

BACKUP_DIR_LOCAL = os.path.join(ROOT_DIR, "_backup_old_ui")
BACKUP_DIR_DEPLOY = os.path.join(DEPLOY_DIR, "_backup_old_ui")

os.makedirs(BACKUP_DIR_LOCAL, exist_ok=True)
if os.path.exists(DEPLOY_DIR):
    os.makedirs(BACKUP_DIR_DEPLOY, exist_ok=True)

# 1. 로컬 개발 환경 루트 index.html 백업 & 교체
local_index = os.path.join(ROOT_DIR, "index.html")
v2_index = os.path.join(ROOT_DIR, "static", "v2", "index.html")

if os.path.exists(local_index):
    backup_target = os.path.join(BACKUP_DIR_LOCAL, "index.html.v1_backup")
    if not os.path.exists(backup_target):
        shutil.copy2(local_index, backup_target)
        print(f"[Backup] {local_index} -> {backup_target} 백업 완료")

shutil.copy2(v2_index, local_index)
print(f"[Promote] {v2_index} -> {local_index} 메인 UI 교체 완료!")

# 2. 배포 리소스 환경(resources) 루트 index.html 백업 & 교체
if os.path.exists(DEPLOY_DIR):
    deploy_index = os.path.join(DEPLOY_DIR, "index.html")
    deploy_v2_index = os.path.join(DEPLOY_DIR, "static", "v2", "index.html")

    if os.path.exists(deploy_index):
        deploy_backup_target = os.path.join(BACKUP_DIR_DEPLOY, "index.html.v1_backup")
        if not os.path.exists(deploy_backup_target):
            shutil.copy2(deploy_index, deploy_backup_target)
            print(f"[Backup] {deploy_index} -> {deploy_backup_target} 백업 완료")

    shutil.copy2(v2_index, deploy_index)
    print(f"[Promote] {v2_index} -> {deploy_index} 배포 폴더 메인 UI 교체 완료!")

print("=== v2 메인 UI 공식 승격 및 백업 완료 ===")
