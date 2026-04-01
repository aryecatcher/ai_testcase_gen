import pandas as pd
from testlink import TestlinkAPIClient
import sys
import os
from typing import List, Optional, Tuple
from loguru import logger
from ...models.domain import TestCase

class TestLinkImporter:
    def __init__(self, server_url: str, api_key: str, project_name: str, author_login: str = "admin"):
        """
        初始化TestLink连接
        :param server_url: TestLink的XML-RPC URL
        :param api_key: TestLink API Key
        :param project_name: 项目名称
        :param author_login: 创建用例的用户名
        """
        # Ensure URL ends with xmlrpc.php if not provided
        if not server_url.endswith("xmlrpc.php"):
            if not server_url.endswith("/"):
                server_url += "/"
            server_url += "lib/api/xmlrpc/v1/xmlrpc.php"
            
        self.server_url = server_url
        self.api_key = api_key
        self.project_name = project_name
        self.author_login = author_login
        
        try:
            self.tl_client = TestlinkAPIClient(server_url, api_key)
            logger.info(f"Initialized TestLink client for {server_url}")
        except Exception as e:
            logger.error(f"Failed to initialize TestLink client: {e}")
            self.tl_client = None

        self.project_id = None
        self.test_suites = {}  # 缓存测试套件ID
        
    def get_projects_list(self) -> List[str]:
        """获取所有可用项目名称列表"""
        if not self.tl_client:
             raise Exception("TestLink client not initialized")
        try:
            projects = self.tl_client.getProjects()
            if not projects:
                return []
            return [p['name'] for p in projects]
        except Exception as e:
            logger.error(f"Error getting project list: {e}")
            raise

    def get_project_id(self):
        """获取项目ID"""
        if not self.tl_client:
            raise Exception("TestLink client not initialized")
            
        try:
            projects = self.tl_client.getProjects()
            available_projects = []
            for project in projects:
                available_projects.append(project['name'])
                if project['name'] == self.project_name:
                    self.project_id = project['id']
                    return self.project_id
            raise Exception(f"项目 '{self.project_name}' 不存在。可用项目: {', '.join(available_projects)}")
        except Exception as e:
            logger.error(f"Error getting project ID: {e}")
            raise

    def create_or_get_test_suite(self, suite_name: str, parent_id: Optional[int] = None) -> int:
        """
        创建或获取测试套件
        :param suite_name: 套件名称
        :param parent_id: 父套件ID（如果是子套件）
        :return: 套件ID
        """
        if not self.tl_client:
            raise Exception("TestLink client not initialized")

        # 检查缓存
        cache_key = f"{parent_id}_{suite_name}"
        if cache_key in self.test_suites:
            return self.test_suites[cache_key]
        
        # 尝试获取已存在的套件
        try:
            suites = self.tl_client.getFirstLevelTestSuitesForTestProject(self.project_id)
            if isinstance(suites, list):
                for suite in suites:
                    if suite['name'] == suite_name:
                        self.test_suites[cache_key] = int(suite['id'])
                        return int(suite['id'])
        except Exception as e:
            # If no suites exist or API error, just proceed to create
            logger.warning(f"Could not fetch suites or none exist: {e}")
        
        # 创建新套件
        try:
            result = self.tl_client.createTestSuite(
                testprojectid=self.project_id,
                testsuitename=suite_name,
                details=f"测试套件: {suite_name}",
                parentid=parent_id
            )
            # Result might be a list or dict depending on API version
            if isinstance(result, list) and len(result) > 0:
                suite_id = int(result[0]['id'])
            elif isinstance(result, dict) and 'id' in result:
                suite_id = int(result['id'])
            else:
                 # Fallback for some versions that return simple dict
                 suite_id = int(result['id'])
                 
            self.test_suites[cache_key] = suite_id
            return suite_id
        except Exception as e:
            logger.error(f"Failed to create test suite '{suite_name}': {e}")
            raise

    def import_test_cases(self, test_cases: List[TestCase]) -> Tuple[int, int]:
        """
        直接从 TestCase 对象列表导入到 TestLink
        :param test_cases: TestCase 对象列表
        :return: (success_count, fail_count)
        """
        if not self.tl_client:
            return 0, len(test_cases)

        # 获取项目ID
        if not self.project_id:
            try:
                self.get_project_id()
            except Exception as e:
                logger.error(f"Cannot proceed without valid project ID: {e}")
                return 0, len(test_cases)
        
        success_count = 0
        fail_count = 0
        
        # Group by module (extracted from req logic or default)
        # We'll use a default suite "AI Generated" if no better structure exists
        # Or map 'dimension' (Functional/Performance) to Suite
        
        for tc in test_cases:
            try:
                suite_name = tc.dimension or "Functional"
                # 创建或获取测试套件
                suite_id = self.create_or_get_test_suite(suite_name)
                
                # 准备测试步骤
                steps = []
                # tc.test_instruction.steps is a list of strings "1. xxx"
                # We need to parse them or just put them as is
                for i, step_str in enumerate(tc.test_instruction.steps):
                    step = {
                        'step_number': i + 1,
                        'actions': step_str,
                        'expected_results': tc.test_instruction.expected_result if i == len(tc.test_instruction.steps) - 1 else "",
                        'execution_type': 1  # Manual
                    }
                    steps.append(step)
                
                # If no steps, add dummy
                if not steps:
                    steps.append({
                        'step_number': 1,
                        'actions': "Execute test",
                        'expected_results': tc.test_instruction.expected_result,
                        'execution_type': 1
                    })

                # 优先级映射
                priority_map = {
                    'P0': 3, 'High': 3,
                    'P1': 2, 'Medium': 2, 'P2': 2,
                    'P3': 1, 'Low': 1
                }
                importance = priority_map.get(tc.priority, 2)
                
                # 创建测试用例
                result = self.tl_client.createTestCase(
                    testcasename=tc.title,
                    testsuiteid=suite_id,
                    testprojectid=self.project_id,
                    authorlogin=self.author_login,
                    summary=f"AI Generated Case for Req: {tc.related_req_id}",
                    steps=steps,
                    preconditions=tc.test_instruction.pre_condition,
                    importance=importance,
                    executiontype=1 # Manual
                )
                
                # 添加关键字（Methodology）
                if tc.methodology:
                    # Not implementing keywords adding for now to avoid complexity if keywords don't exist
                    pass
                
                logger.info(f"✓ Successfully imported: {tc.title}")
                success_count += 1
                
            except Exception as e:
                logger.error(f"✗ Failed to import {tc.title}: {e}")
                fail_count += 1
        
        return success_count, fail_count
