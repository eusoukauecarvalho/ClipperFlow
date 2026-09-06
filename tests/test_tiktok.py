"""Testes da integração com o TikTok: fragmentação, PKCE, token e privacidade."""

from __future__ import annotations

import base64
import hashlib
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import tiktok_auth  # noqa: E402
from tiktok_upload import (  # noqa: E402
    CHUNK_MAX,
    CHUNK_ULTIMO_MAX,
    MB,
    TikTokError,
    escolher_privacidade,
    montar_legenda,
    planejar_chunks,
)


class PlanejarChunksTests(unittest.TestCase):
    """A regra do TikTok usa PISO na divisão, não teto — com teto sobra um
    fragmento a mais e o upload é recusado."""

    def test_video_pequeno_vai_inteiro(self):
        plano = planejar_chunks(3 * MB)

        self.assertEqual(plano.total_chunk_count, 1)
        self.assertEqual(plano.chunk_size, 3 * MB)
        self.assertEqual(plano.faixas, ((0, 3 * MB - 1),))

    def test_video_de_ate_64mb_continua_em_um_fragmento(self):
        plano = planejar_chunks(CHUNK_MAX)

        self.assertEqual(plano.total_chunk_count, 1)

    def test_usa_piso_e_nao_teto_na_divisao(self):
        # 200 MB / 64 MB = 3.125 → 3 fragmentos, nunca 4.
        plano = planejar_chunks(200 * MB)

        self.assertEqual(plano.total_chunk_count, 3)

    def test_ultimo_fragmento_absorve_o_resto(self):
        tamanho = 200 * MB
        plano = planejar_chunks(tamanho)
        inicio, fim = plano.faixas[-1]

        self.assertEqual(fim, tamanho - 1)
        self.assertGreater(fim - inicio + 1, plano.chunk_size)

    def test_faixas_cobrem_o_arquivo_sem_buraco_nem_sobreposicao(self):
        for tamanho in (1 * MB, CHUNK_MAX, 130 * MB, 200 * MB, 777 * MB):
            with self.subTest(tamanho=tamanho):
                plano = planejar_chunks(tamanho)
                self.assertEqual(plano.faixas[0][0], 0)
                self.assertEqual(plano.faixas[-1][1], tamanho - 1)
                for anterior, seguinte in zip(plano.faixas, plano.faixas[1:]):
                    self.assertEqual(seguinte[0], anterior[1] + 1)

    def test_ultimo_fragmento_nunca_passa_de_128mb(self):
        for tamanho in (65 * MB, 127 * MB, 200 * MB, 1000 * MB, 5000 * MB):
            with self.subTest(tamanho=tamanho):
                plano = planejar_chunks(tamanho)
                inicio, fim = plano.faixas[-1]
                self.assertLessEqual(fim - inicio + 1, CHUNK_ULTIMO_MAX)

    def test_numero_de_faixas_bate_com_o_declarado(self):
        plano = planejar_chunks(500 * MB)

        self.assertEqual(len(plano.faixas), plano.total_chunk_count)

    def test_arquivo_vazio_e_recusado(self):
        with self.assertRaises(TikTokError):
            planejar_chunks(0)


class EscolherPrivacidadeTests(unittest.TestCase):
    """App não auditado não recebe PUBLIC_TO_EVERYONE — forçar faria falhar."""

    def test_usa_a_desejada_quando_disponivel(self):
        info = {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}

        self.assertEqual(escolher_privacidade(info, "PUBLIC_TO_EVERYONE"), "PUBLIC_TO_EVERYONE")

    def test_cai_para_self_only_quando_publico_indisponivel(self):
        info = {"privacy_level_options": ["SELF_ONLY"]}

        self.assertEqual(escolher_privacidade(info, "PUBLIC_TO_EVERYONE"), "SELF_ONLY")

    def test_prefere_self_only_entre_as_alternativas(self):
        info = {"privacy_level_options": ["FOLLOWER_OF_CREATOR", "SELF_ONLY"]}

        self.assertEqual(escolher_privacidade(info, "PUBLIC_TO_EVERYONE"), "SELF_ONLY")

    def test_sem_opcoes_e_erro_explicito(self):
        with self.assertRaises(TikTokError):
            escolher_privacidade({"privacy_level_options": []}, "SELF_ONLY")


class MontarLegendaTests(unittest.TestCase):
    def test_junta_titulo_descricao_e_hashtags(self):
        legenda = montar_legenda({
            "titulo": "Título", "descricao": "Uma frase.",
            "tags": ["duas palavras", "outra"],
        })

        self.assertIn("Título", legenda)
        self.assertIn("#duaspalavras", legenda)  # espaço vira nada dentro da hashtag

    def test_respeita_o_limite_de_2200(self):
        legenda = montar_legenda({"titulo": "x" * 5000, "tags": []})

        self.assertLessEqual(len(legenda), 2200)

    def test_campos_ausentes_nao_deixam_linhas_vazias(self):
        self.assertEqual(montar_legenda({"titulo": "Só o título"}), "Só o título")


class PkceTests(unittest.TestCase):
    def test_challenge_e_o_sha256_do_verifier_em_base64url(self):
        verificador = tiktok_auth.gerar_code_verifier()

        desafio = tiktok_auth.derivar_code_challenge(verificador)

        esperado = base64.urlsafe_b64encode(
            hashlib.sha256(verificador.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")
        self.assertEqual(desafio, esperado)

    def test_challenge_nao_tem_padding(self):
        desafio = tiktok_auth.derivar_code_challenge(tiktok_auth.gerar_code_verifier())

        self.assertNotIn("=", desafio)

    def test_verifier_respeita_o_tamanho_do_padrao(self):
        verificador = tiktok_auth.gerar_code_verifier()

        self.assertGreaterEqual(len(verificador), 43)
        self.assertLessEqual(len(verificador), 128)

    def test_verifiers_sao_distintos(self):
        self.assertNotEqual(tiktok_auth.gerar_code_verifier(), tiktok_auth.gerar_code_verifier())

    def test_url_de_autorizacao_leva_os_parametros_obrigatorios(self):
        url = tiktok_auth.montar_url_autorizacao(
            "chave", "http://localhost/cb", "desafio", "estado"
        )

        for esperado in ("client_key=chave", "response_type=code", "code_challenge=desafio",
                         "code_challenge_method=S256", "state=estado"):
            self.assertIn(esperado, url)
        self.assertIn("video.publish", url)


class TokenTests(unittest.TestCase):
    def _tokens(self, expira_em):
        return tiktok_auth.TikTokTokens(
            access_token="a", refresh_token="r", open_id="o", scope="s",
            expires_at=time.time() + expira_em,
        )

    def test_token_com_folga_nao_esta_expirado(self):
        self.assertFalse(self._tokens(3600).expirado)

    def test_token_dentro_da_margem_conta_como_expirado(self):
        # Renova ANTES de morrer: um upload longo não pode perder o token no meio.
        self.assertTrue(self._tokens(60).expirado)

    def test_grava_e_le_de_volta(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "t.json"
            tiktok_auth.salvar(caminho, self._tokens(3600))

            lido = tiktok_auth.carregar(caminho)

        self.assertEqual(lido.access_token, "a")
        self.assertEqual(lido.refresh_token, "r")

    def test_arquivo_de_token_fica_com_permissao_restrita(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "t.json"
            tiktok_auth.salvar(caminho, self._tokens(3600))

            self.assertEqual(caminho.stat().st_mode & 0o777, 0o600)

    def test_arquivo_ausente_devolve_none(self):
        self.assertIsNone(tiktok_auth.carregar(Path("/nao/existe.json")))

    def test_arquivo_corrompido_devolve_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = Path(tmp) / "t.json"
            caminho.write_text("{lixo", encoding="utf-8")

            self.assertIsNone(tiktok_auth.carregar(caminho))

    def test_sem_token_salvo_o_erro_diz_o_que_fazer(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(tiktok_auth.TikTokAuthError) as ctx:
                tiktok_auth.token_valido(Path(tmp) / "ausente.json", "k", "s")

        self.assertIn("authorize", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
