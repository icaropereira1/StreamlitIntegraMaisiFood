import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import time
import io

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Gestor de PDV iFood", page_icon="🍔", layout="wide")
st.title("🍔 Gestor de Códigos PDV - iFood")

# --- ENDPOINTS BASE ---
URL_AUTH = "https://merchant-api.ifood.com.br/authentication/v1.0/oauth/token"
URL_CATALOG_BASE = "https://merchant-api.ifood.com.br/catalog/v1.0/merchants"

# --- SIDEBAR: CREDENCIAIS ---
st.sidebar.header("🔑 Credenciais da API")
st.sidebar.markdown("Insira os dados da loja para conectar.")
client_id = st.sidebar.text_input("Client ID", type="password")
client_secret = st.sidebar.text_input("Client Secret", type="password")
merchant_id = st.sidebar.text_input("Merchant ID (ID da Loja)")

def get_token(cid, csec):
    payload = {"grantType": "client_credentials", "clientId": cid, "clientSecret": csec}
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(URL_AUTH, data=payload, headers=headers)
    if response.status_code == 200:
        return response.json()['accessToken']
    else:
        st.error(f"Erro de Autenticação: {response.text}")
        return None

# ==========================================
# FUNÇÕES: ABA 1 (EXTRAIR)
# ==========================================
def extrair_cardapio(token, m_id):
    headers = {"Authorization": f"Bearer {token}"}
    
    # 1. Busca Catálogo
    r_catalogs = requests.get(f"{URL_CATALOG_BASE}/{m_id}/catalogs", headers=headers)
    if r_catalogs.status_code != 200:
        raise Exception(f"Erro ao listar catálogos: {r_catalogs.text}")
        
    catalogs = r_catalogs.json()
    if not catalogs:
        raise Exception("Nenhum catálogo encontrado para esta loja.")
    
    catalog_id = catalogs[0]['catalogId']

    # 2. Baixa Árvore
    url_categories = f"{URL_CATALOG_BASE}/{m_id}/catalogs/{catalog_id}/categories?includeItems=true"
    r_categories = requests.get(url_categories, headers=headers)
    categories = r_categories.json()

    # 3. Processamento
    rows = []
    ids_processados = set()

    for category in categories:
        cat_name = category.get('name', 'SEM CATEGORIA')
        for item in category.get('items', []):
            prod_id = item.get('id')
            
            if prod_id not in ids_processados:
                ids_processados.add(prod_id)
                rows.append({
                    "Nível": "PRODUTO",
                    "Categoria": cat_name,
                    "Produto Pai": item.get('name'),
                    "Item / Opcional": item.get('name'),
                    "Código PDV (externalCode)": item.get('externalCode', ''),
                    "Status": item.get('status', ''),
                    "ID iFood": prod_id
                })
            
            current_prod_name = item.get('name')

            for group in item.get('optionGroups', []):
                group_name = group.get('name')
                for option in group.get('options', []):
                    opt_id = option.get('id')
                    if opt_id not in ids_processados:
                        ids_processados.add(opt_id)
                        rows.append({
                            "Nível": "COMPLEMENTO",
                            "Categoria": cat_name,
                            "Produto Pai": current_prod_name,
                            "Item / Opcional": f"[{group_name}] {option.get('name')}",
                            "Código PDV (externalCode)": option.get('externalCode', ''),
                            "Status": option.get('status', ''),
                            "ID iFood": opt_id
                        })
    return pd.DataFrame(rows)

def gerar_excel_em_memoria(df):
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter')
    df.to_excel(writer, sheet_name='Cardapio', index=False)

    workbook  = writer.book
    worksheet = writer.sheets['Cardapio']

    formato_bloqueado = workbook.add_format({'locked': True, 'bg_color': '#f2f2f2'})
    formato_liberado = workbook.add_format({'locked': False})

    colunas_para_bloquear = ["ID iFood", "Status", "Nível"]

    for col_num, col_name in enumerate(df.columns):
        max_len = max(df[col_name].astype(str).map(len).max(), len(str(col_name))) + 2
        if max_len > 60: max_len = 60
        
        if col_name in colunas_para_bloquear:
            worksheet.set_column(col_num, col_num, max_len, formato_bloqueado)
        else:
            worksheet.set_column(col_num, col_num, max_len, formato_liberado)

    worksheet.autofilter(0, 0, len(df), len(df.columns) - 1)
    worksheet.protect('xicaroehfoda', {
        'autofilter': True,
        'objects': True,
        'select_locked_cells': True,
        'select_unlocked_cells': True
    })
    
    writer.close()
    return output.getvalue()

# ==========================================
# FUNÇÕES: ABA 2 (ATUALIZAR)
# ==========================================
def mapear_codigos_atuais(token, m_id):
    headers = {"Authorization": f"Bearer {token}"}
    url_base_v2 = f"https://merchant-api.ifood.com.br/catalog/v2.0/merchants/{m_id}"
    
    r_cat = requests.get(f"{url_base_v2}/catalogs", headers=headers)
    if r_cat.status_code != 200: raise Exception("Erro ao listar catálogos")
    catalog_id = r_cat.json()[0]['catalogId']
    
    url_tree = f"{url_base_v2}/catalogs/{catalog_id}/categories?includeItems=true"
    r_tree = requests.get(url_tree, headers=headers)
    categories = r_tree.json()
    
    mapa_atual = {}
    for cat in categories:
        for item in cat.get('items', []):
            item_id = item.get('id')
            item_code = item.get('externalCode', '')
            mapa_atual[item_id] = str(item_code) if item_code else ""
            
            for group in item.get('optionGroups', []):
                for option in group.get('options', []):
                    opt_id = option.get('id')
                    opt_code = option.get('externalCode', '')
                    mapa_atual[opt_id] = str(opt_code) if opt_code else ""
    return mapa_atual

def atualizar_item(token, m_id, id_obj, novo_codigo, nivel):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url_base_v2 = f"https://merchant-api.ifood.com.br/catalog/v2.0/merchants/{m_id}"
    
    if nivel == 'PRODUTO':
        url = f"{url_base_v2}/items/externalCode"
        payload = {"itemId": str(id_obj), "externalCode": str(novo_codigo)}
    else: 
        url = f"{url_base_v2}/options/externalCode"
        payload = {"optionId": str(id_obj), "externalCode": str(novo_codigo)}
        
    return requests.patch(url, json=payload, headers=headers)


# ==========================================
# INTERFACE DO USUÁRIO (TABS)
# ==========================================
tab1, tab2 = st.tabs(["📥 1. Baixar Planilha", "📤 2. Atualizar PDVs"])

with tab1:
    st.header("Baixar Cardápio Atual")
    st.write("Gere a planilha bloqueada contendo o cardápio atual do iFood.")
    
    if st.button("Gerar Planilha"):
        if not client_id or not client_secret or not merchant_id:
            st.warning("Preencha todas as credenciais na barra lateral primeiro.")
        else:
            with st.spinner("Autenticando e montando a planilha. Aguarde..."):
                try:
                    token = get_token(client_id, client_secret)
                    if token:
                        df_cardapio = extrair_cardapio(token, merchant_id)
                        excel_data = gerar_excel_em_memoria(df_cardapio)
                        
                        data_str = datetime.now().strftime("%Y-%m-%d_%H-%M")
                        st.success("Planilha gerada com sucesso!")
                        st.download_button(
                            label="📥 Clique aqui para Baixar o Excel",
                            data=excel_data,
                            file_name=f"Cardapio_iFood_{merchant_id}_{data_str}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                except Exception as e:
                    st.error(f"Erro fatal: {e}")

with tab2:
    st.header("Atualizar Códigos PDV")
    st.write("Faça o upload da planilha editada. O sistema atualizará apenas o que foi alterado.")
    
    arquivo_upload = st.file_uploader("Selecione a planilha Excel", type=["xlsx"])
    
    if arquivo_upload is not None:
        if st.button("🚀 Iniciar Atualização no iFood"):
            if not client_id or not client_secret or not merchant_id:
                st.warning("Preencha todas as credenciais na barra lateral primeiro.")
            else:
                try:
                    token = get_token(client_id, client_secret)
                    if not token:
                        st.stop()
                        
                    st.info("📥 Baixando cardápio atual para evitar atualizações redundantes...")
                    mapa_atual = mapear_codigos_atuais(token, merchant_id)
                    
                    df = pd.read_excel(arquivo_upload, dtype={'Código PDV (externalCode)': str, 'ID iFood': str})
                    
                    pulados = 0
                    atualizados = 0
                    erros = 0
                    
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    total_linhas = len(df)
                    
                    for index, row in df.iterrows():
                        id_ifood = row['ID iFood']
                        novo_codigo = row['Código PDV (externalCode)']
                        nivel = row['Nível']
                        nome = row['Item / Opcional']
                        
                        if pd.isna(id_ifood) or pd.isna(novo_codigo) or str(novo_codigo).lower() == 'nan':
                            continue
                            
                        novo_codigo = str(novo_codigo).strip()
                        id_ifood = str(id_ifood).strip()
                        
                        codigo_no_ifood = mapa_atual.get(id_ifood)
                        
                        if codigo_no_ifood == novo_codigo:
                            pulados += 1
                        else:
                            status_text.text(f"Atualizando: {nome[:30]}... ({codigo_no_ifood} ➡️ {novo_codigo})")
                            resp = atualizar_item(token, merchant_id, id_ifood, novo_codigo, nivel)
                            
                            if resp.status_code == 200:
                                map_atual = novo_codigo
                                atualizados += 1
                            elif resp.status_code == 429:
                                st.warning("Limite da API atingido (Rate Limit). Pausando por 60 segundos...")
                                time.sleep(60)
                                resp = atualizar_item(token, merchant_id, id_ifood, novo_codigo, nivel)
                                if resp.status_code == 200:
                                    atualizados += 1
                                else:
                                    erros += 1
                            else:
                                erros += 1
                            
                            time.sleep(0.4) # Respeito ao Rate Limit do iFood
                        
                        progress_bar.progress((index + 1) / total_linhas)

                    status_text.text("Processamento concluído!")
                    st.success("✅ Atualização Finalizada!")
                    
                    col1, col2, col3 = st.columns(3)
                    col1.metric("⏭️ Pulados (Já estavam certos)", pulados)
                    col2.metric("✅ Atualizados", atualizados)
                    col3.metric("❌ Erros", erros)
                    
                except Exception as e:
                    st.error(f"Erro ao processar: {e}")