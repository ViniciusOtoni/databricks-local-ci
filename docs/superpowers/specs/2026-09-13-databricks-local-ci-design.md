# Design: Framework de CI local para validar apps Databricks (Python) antes do deploy

## Contexto

Hoje, a única forma de saber se uma aplicação Python vai quebrar no Databricks — por
incompatibilidade de versão de biblioteca com o Databricks Runtime (DBR) ou por erro de
lógica no uso de Spark/Delta — é fazer o deploy real (ou rodar manualmente num cluster de
dev) e esperar o cluster subir. Isso é lento (minutos de cold start), custa DBU, e faz com
que bugs só apareçam tarde no ciclo, depois do merge.

O gatilho foi ver que a Databricks publica imagens oficiais no Docker Hub
(`databricksruntime/*`) contendo o stack Python do DBR (Spark + Delta no classpath, cliente
MLflow nas variantes ML). A ideia é usar essas imagens para rodar uma esteira de teste de
integração *local* antes do deploy: só some pro Databricks o que passou no teste local;
se falhar, quem escreveu o código corrige antes.

### Validação da premissa (pesquisa feita nesta sessão)

- As imagens `databricksruntime/*` são reais e mantidas pela Databricks
  ([Docker Hub](https://hub.docker.com/u/databricksruntime),
  [Dockerfiles no GitHub](https://github.com/databricks/containers)), mas seu propósito
  documentado é servir de **base para Databricks Container Services** (imagens customizadas
  que rodam *dentro* de um cluster Databricks), não um emulador standalone do workspace.
- Isso significa: dá pra rodar Spark+Delta localmente com a mesma versão do DBR de produção
  (útil e real), mas **não existe** Unity Catalog, control plane (Jobs API, secrets,
  policies, endpoints) nem MLflow Tracking Server hospedado dentro do container.
- Risco de manutenção: só tags `-LTS` recebem patch regular; as demais são "exemplos". Há
  histórico de demora da Databricks em publicar tags de runtimes novos no Docker Hub.
- Conclusão: o projeto é factível, mas como **container versionado do DBR para pegar bugs
  de dependência/versão/lógica rápido e de graça**, não como réplica completa do ambiente
  Databricks.

## Escopo

**Dentro (v1):**
- Aplicações Python empacotadas como **wheel**, rodando como **Databricks Job** (sem
  notebooks).
- Build do wheel feito **dentro** do container `databricksruntime` (garante mesma versão de
  Python/glibc/libs nativas do DBR real — evita "passou no CI, quebrou no Databricks").
- Teste de integração como **run real**: o teste dispara, via `subprocess`, o mesmo entry
  point (CLI/console-script) que o Databricks Job aciona em produção — não um import
  interno nem mock. O processo sobe Spark de verdade e escreve Delta de verdade em paths
  locais; **pytest** entra depois só para orquestrar o setup/teardown e validar o resultado
  (ler a tabela Delta gerada, checar schema/contagem/valores).
- Deploy real via **Databricks Asset Bundles** (`databricks bundle deploy`), autenticado por
  **OIDC + Service Principal** (sem secret de longa duração).
- Distribuído como **reusable workflow do GitHub Actions**, consumido por múltiplos
  projetos-cliente (este repo nasce como *framework*, não como o CI de um projeto só).
- Versão do DBR alvo é **configurável por projeto-cliente** (input do workflow), não fixa no
  framework nem em matriz mantida centralmente.

**Fora (v1) — e por quê:**
- **Notebooks e Lakeflow Declarative Pipelines (ex-DLT)**: motores gerenciados que não rodam
  fora do control plane; testar 100% local não é viável.
- **Unity Catalog, scopes, policies, endpoints**: não existem no container; explicitamente
  fora do que este projeto se propõe a validar.
- **Databricks Connect / `SPARK_REMOTE`**: avaliado e descartado. Para dar acesso a UC real
  precisaria apontar pra um endpoint real (volta a custar DBU/depender de rede — o problema
  que o projeto quer evitar), e como o workload é Job/wheel rodando *dentro* do cluster, o
  código de produção nunca passa pelo Spark Connect — testar por aí validaria um caminho que
  não existe em produção.
- **Camada de abstração automática de storage** (interceptar `spark.read.table` e redirecionar
  sozinho): descartada em favor de parametrização explícita — menos "mágica", mais previsível.

## Arquitetura

**Repositório "framework" contém:**

1. **`Dockerfile`** baseado em `databricksruntime/python:<tag>`, parametrizável por
   build-arg de versão do DBR.
2. **Reusable workflow** `.github/workflows/databricks-ci.yml` (`workflow_call`) com inputs
   como `dbr_version`, `bundle_target`, `package_dir`:
   - **Job `build-and-test`**: sobe o container, builda o wheel *dentro* dele e instala,
     depois roda `pytest` *dentro* dele. Cada teste dispara via `subprocess` o entry point
     real da wheel (mesmo comando que o Job do Databricks executaria), passando parâmetros
     que apontam para paths Delta locais em vez de `catalog.schema.tabela`; o pytest só
     entra depois pra ler o resultado e validar schema/contagem/valores.
   - **Job `deploy`** (`needs: build-and-test`): roda `databricks bundle deploy`
     autenticado via OIDC, só executa se o job anterior passar.
3. **Fixtures pytest compartilhadas**: um helper que invoca o entry point via `subprocess`
   com os parâmetros certos e devolve o resultado (exit code, stdout, path da tabela
   gerada), e uma fixture de warehouse/diretório temporário para as tabelas Delta locais —
   infraestrutura de bootstrap, não abstração que esconde a lógica do projeto-cliente.
4. **README + repo de exemplo** mostrando como um projeto-cliente consome o reusable
   workflow e estrutura seu `databricks.yml` (DAB).

**Contrato exigido de cada projeto-cliente:**
- Expõe um **entry point único** (CLI/console-script) — o mesmo que o Databricks Job
  invoca — e recebe `catalog.schema.tabela` (ou paths) **como parâmetro**, nunca hardcoded.
  Em produção aponta pro Unity Catalog real; no teste local, o subprocess passa paths Delta
  locais. Isso também é ganho colateral de portabilidade entre ambientes (dev/staging/prod).
- Testes ficam em `tests/`, usam o helper de subprocess + fixture de warehouse local
  fornecidos pelo framework.
- Projeto declara a versão do DBR alvo como input ao chamar o reusable workflow.

## Fluxo

PR aberto → reusable workflow chamado → `build-and-test` builda a imagem (ou usa uma já
publicada), builda o wheel dentro dela e instala → pytest dispara o entry point real da
wheel via subprocess, com paths locais, e valida a tabela Delta resultante → se falhar, PR
fica bloqueado (branch protection) e quem escreveu corrige → se passar, `deploy` roda
`databricks bundle deploy` autenticado via OIDC pro workspace de destino.

## Ganhos

- Feedback em segundos/minutos, sem esperar cluster subir e sem custo de DBU.
- Pega quebras de compatibilidade de dependência (pandas/numpy/pyarrow etc.) e bugs de
  lógica Spark/Delta antes do deploy, rodando o pacote real dentro da versão exata do DBR
  de produção.
- Por ser framework reutilizável, qualquer novo projeto Databricks ganha esteira de teste
  padronizada com baixo custo de adoção — não fica restrito a um projeto só.
- Deploy gated: só chega ao Databricks o que passou no teste local, reduzindo deploys
  quebrados.
- A regra de parametrizar nomes de tabela força um código mais portável entre ambientes,
  ganho de arquitetura que vai além do teste em si.

## Dor que resolve

Hoje não existe rede de segurança entre "código escrito" e "código rodando no Databricks"
que não passe por gastar cluster real. Isso atrasa a detecção de erros óbvios (dependência
incompatível, erro de sintaxe Spark, schema errado) para depois do merge/deploy, gerando
retrabalho e ciclos de feedback lentos e caros.

## Riscos conhecidos

- Depende da Databricks continuar publicando/atualizando a tag do DBR usada. Mitigação:
  pinar tag exata + monitorar releases; se uma versão sumir do Docker Hub, fallback é
  buildar a partir do Dockerfile de referência em `github.com/databricks/containers`.
- MLflow dentro do container só loga em file-store local — não valida o Tracking Server
  real hospedado pela Databricks.
- Cobertura da v1 é só Job/wheel Python "puro" — notebooks e Lakeflow/DLT ficam de fora.

## Verificação / prova de conceito mínima

1. `docker pull databricksruntime/python:<tag>` e rodar um script smoke test
   (`import pyspark, delta, mlflow`; criar `SparkSession` com Delta habilitado) para
   confirmar que Spark+Delta+cliente MLflow funcionam dentro do container.
2. Buildar um wheel de exemplo dentro do container e instalar no Python do container.
3. Rodar um teste pytest de exemplo que dispara o entry point da wheel via subprocess
   (parâmetros apontando pra path local), e depois lê a tabela Delta resultante validando
   schema e contagem de linhas — confirmando o padrão de "run real" ponta a ponta.
4. Criar um repo-exemplo mínimo consumindo o reusable workflow via
   `uses: <org>/<framework-repo>/.github/workflows/databricks-ci.yml@<tag>`, confirmando
   que os jobs `build-and-test` e `deploy` aparecem e encadeiam corretamente (deploy pode
   rodar em modo `validate`/dry-run nesse smoke inicial).
5. Validar a autenticação OIDC contra um workspace de teste/sandbox antes de usar em
   produção.

## Próximos passos

Depois de aprovado este desenho, o próximo passo é usar a skill `writing-plans` para
detalhar o plano de implementação: estrutura de arquivos do repo framework, ordem de
construção, e como testar o próprio framework antes de ele testar outros projetos.
