import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import psycopg
from psycopg import sql

from deploy.manage import HERE, read_config, verify_source
from deploy.migrate import apply, MIGRATIONS, ROOT
from deploy.runtime import database_url
from deploy.schema_baseline import adopt, catalog_fingerprint


class DeploymentConfigurationTests(unittest.TestCase):
    def test_production_build_refuses_wrong_commit_and_dirty_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "deploy").mkdir()
            for command in (["git", "init", "-q"], ["git", "config", "user.email", "fixture@example.invalid"], ["git", "config", "user.name", "Fixture"]):
                subprocess.run(command, cwd=root, check=True, capture_output=True)
            (root / "app.py").write_text("pass\n")
            subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
            subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"], cwd=root, check=True)
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
            with patch("deploy.manage.HERE", root / "deploy"):
                self.assertEqual(verify_source({"APP_COMMIT": commit})["source_state"], "clean")
                with self.assertRaisesRegex(ValueError, "differs"):
                    verify_source({"APP_COMMIT": "0" * 40})
                (root / "app.py").write_text("raise RuntimeError()\n")
                with self.assertRaisesRegex(ValueError, "clean"):
                    verify_source({"APP_COMMIT": commit})
                self.assertEqual(verify_source({"APP_COMMIT": commit}, local=True)["source_state"], "uncommitted-local-rehearsal")
    def test_configuration_rejects_placeholders_and_interpolation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.env"
            config.write_text((HERE / "config.env.example").read_text())
            with self.assertRaises(ValueError):
                read_config(config)
            config.write_text("API_HOSTNAME=$(unsafe)\n")
            with self.assertRaises(ValueError):
                read_config(config)

    def test_database_secret_is_encoded_not_interpolated(self):
        with patch("deploy.runtime.secret", return_value="a@b:/?" * 5), patch.dict(os.environ, {"PGHOST": "db", "PGDATABASE": "research"}):
            value = database_url("research_api_login", "api_password")
            self.assertIn("a%40b%3A%2F%3F", value)
            self.assertNotIn("a@b:/?", value)

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose is unavailable")
    def test_compose_keeps_database_and_api_private(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / "config.env"
            env.write_text((HERE / "config.env.example").read_text().replace("REPLACE_WITH_REVIEWED_GIT_COMMIT", "a" * 40).replace("REPLACE_WITH_API_HOSTNAME", "api.example.invalid"))
            result = subprocess.run(["docker", "compose", "--env-file", str(env), "-f", str(HERE / "compose.yaml"), "config", "--format", "json"], capture_output=True, text=True, check=True)
            config = json.loads(result.stdout)
            services = config["services"]
            self.assertNotIn("ports", services["db"])
            self.assertNotIn("ports", services["api"])
            self.assertNotIn("ports", services["worker"])
            self.assertEqual({secret["source"] for secret in services["api"]["secrets"]}, {"api_password", "session_secret"})
            self.assertEqual({secret["source"] for secret in services["worker"]["secrets"]}, {"worker_password"})
            self.assertIn("@sha256:", services["db"]["image"])
            self.assertIn("@sha256:", services["proxy"]["image"])
            self.assertTrue(config["networks"]["database"]["internal"])
            local = subprocess.run(["docker", "compose", "--env-file", str(env), "-f", str(HERE / "compose.yaml"), "-f", str(HERE / "compose.local.yaml"), "config", "--format", "json"], capture_output=True, text=True, check=True)
            ports = json.loads(local.stdout)["services"]["proxy"]["ports"]
            self.assertEqual(len(ports), 1)
            self.assertEqual(ports[0]["host_ip"], "127.0.0.1")


@unittest.skipUnless(os.environ.get("RESEARCH_DEPLOY_TEST_DATABASE_URL"), "Set an empty isolated *_deploy_test database on a disposable cluster")
class MigrationIntegrationTests(unittest.TestCase):
    def test_atomic_migration_repeat_permissions_and_corruption(self):
        url = os.environ["RESEARCH_DEPLOY_TEST_DATABASE_URL"]
        passwords = {name: name + "-fixture-only-" + "x" * 24 for name in ("api", "worker", "importer", "backup")}
        with psycopg.connect(url) as connection:
            database = connection.execute("SELECT current_database()").fetchone()[0]
            if not database.endswith("_deploy_test"):
                self.fail("Refusing a non-test database")
            self.assertIsNone(connection.execute("SELECT to_regnamespace('research')").fetchone()[0], "Use a fresh database")
            connection.commit()
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for name in MIGRATIONS:
                    (root / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / name, root / name)
                failed = "migrations/999_failure.sql"
                (root / failed).write_text("CREATE TABLE public.must_roll_back(id integer); SELECT missing_function();")
                with self.assertRaises(psycopg.Error):
                    apply(connection, passwords, root, (*MIGRATIONS, failed))
                self.assertIsNone(connection.execute("SELECT to_regnamespace('research')").fetchone()[0])
                self.assertIsNone(connection.execute("SELECT to_regclass('public.must_roll_back')").fetchone()[0])
                connection.commit()
                first = apply(connection, passwords, root)
                self.assertEqual(first["applied"], list(MIGRATIONS))
                for role in ("research_api_login", "research_worker_login", "research_importer_login", "research_backup_login"):
                    connection.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(role)))
                connection.commit()
                second = apply(connection, passwords, root)
                self.assertEqual(second["applied"], [])
                self.assertEqual(second["already_applied"], len(MIGRATIONS))
                for role in ("research_api_login", "research_worker_login", "research_importer_login", "research_backup_login"):
                    self.assertTrue(connection.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname=%s", (role,)).fetchone()[0])
                for role in ("research_api_login", "research_worker_login", "research_backup_login"):
                    result = connection.execute("SELECT has_table_privilege(%s,'research.publications','INSERT'),rolsuper,rolcreatedb FROM pg_roles WHERE rolname=%s", (role, role)).fetchone()
                    self.assertEqual(result, (False, False, False))
                self.assertTrue(connection.execute("SELECT has_table_privilege('research_importer_login','research.publications','INSERT')").fetchone()[0])
                self.assertTrue(connection.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname='research_backup_login'").fetchone()[0])
                self.assertFalse(connection.execute("SELECT has_table_privilege('anon','research.artifacts','SELECT')").fetchone()[0])
                connection.commit()
                file = root / MIGRATIONS[0]
                file.write_text(file.read_text() + "\n-- changed after application\n")
                with self.assertRaisesRegex(ValueError, "Previously applied migration changed"):
                    apply(connection, passwords, root)
                self.assertEqual(connection.execute("SELECT count(*) FROM public.research_schema_migrations").fetchone()[0], len(MIGRATIONS))


@unittest.skipUnless(os.environ.get("RESEARCH_ADOPT_TEST_DATABASE_URL"), "Set a fresh isolated *_adopt_test database on a disposable cluster")
class AdoptionIntegrationTests(unittest.TestCase):
    def test_exact_schema_adoption_and_drift_rejection(self):
        from deploy.migrate import migration_body
        url = os.environ["RESEARCH_ADOPT_TEST_DATABASE_URL"]
        passwords = {name: name + "-fixture-only-" + "x" * 24 for name in ("api", "worker", "importer", "backup")}
        with psycopg.connect(url) as connection:
            self.assertTrue(connection.execute("SELECT current_database()").fetchone()[0].endswith("_adopt_test"))
            self.assertIsNone(connection.execute("SELECT to_regnamespace('research')").fetchone()[0])
            for name in MIGRATIONS[:5]:
                connection.execute(migration_body((ROOT / name).read_text()))
            connection.commit()
            connection.execute("ALTER TABLE research.datasets ADD COLUMN unexpected text")
            connection.commit()
            with self.assertRaisesRegex(ValueError, "exactly match"):
                adopt(connection, ROOT)
            self.assertIsNone(connection.execute("SELECT to_regclass('public.research_schema_migrations')").fetchone()[0])
            connection.execute("ALTER TABLE research.datasets DROP COLUMN unexpected")
            connection.commit()
            # A dropped physical column changes attnum history only when later columns follow it;
            # the recognized schema compares visible column definitions and rejects real drift.
            connection.execute("ALTER TABLE research.datasets ADD CONSTRAINT datasets_latest_publication_id_fkey FOREIGN KEY(latest_publication_id) REFERENCES research.publications(id)")
            connection.commit()
            result = adopt(connection, ROOT)
            self.assertEqual(result["verified_migrations"], 5)
            migrated = apply(connection, passwords)
            self.assertEqual(migrated["applied"], list(MIGRATIONS[5:]))
            self.assertEqual(apply(connection, passwords)["applied"], [])


if __name__ == "__main__":
    unittest.main()
