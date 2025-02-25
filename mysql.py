import os
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv

# Загрузка параметров подключения из .env файла
load_dotenv()

def get_connection():
    """
    Устанавливает соединение с MySQL, используя параметры из переменных окружения.
    Переменные: MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB.
    """
    try:
        connection = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=os.getenv("MYSQL_PORT", "3306"),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DB", "test")
        )
        if connection.is_connected():
            return connection
    except Error as e:
        print(f"Ошибка подключения к MySQL: {e}")
        return None

def execute_query(query, params=None):
    """
    Выполняет запрос на изменение данных (INSERT, UPDATE, DELETE).
    Возвращает идентификатор последней вставленной записи (для INSERT) или None.
    """
    connection = get_connection()
    if connection:
        try:
            cursor = connection.cursor()
            cursor.execute(query, params)
            connection.commit()
            return cursor.lastrowid
        except Error as e:
            print(f"Ошибка выполнения запроса: {e}")
        finally:
            cursor.close()
            connection.close()
    return None

def execute_read_query(query, params=None):
    """
    Выполняет запрос на выборку данных (SELECT) и возвращает результат в виде списка словарей.
    """
    connection = get_connection()
    result = None
    if connection:
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(query, params)
            result = cursor.fetchall()
            return result
        except Error as e:
            print(f"Ошибка выполнения запроса: {e}")
        finally:
            cursor.close()
            connection.close()
    return result

# Пример использования
if __name__ == "__main__":
    # Пример создания таблицы, если она не существует
    create_table_query = """
    CREATE TABLE IF NOT EXISTS oils (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        volume FLOAT,
        price FLOAT
    );
    """
    execute_query(create_table_query)
    print("Таблица 'oils' создана (если не существовала).")
    
    # Пример вставки новой записи
    insert_query = "INSERT INTO oils (name, volume, price) VALUES (%s, %s, %s)"
    new_id = execute_query(insert_query, ("Лаванда", 30.0, 200.0))
    print(f"Добавлена запись с id: {new_id}")
    
    # Пример выборки данных
    select_query = "SELECT * FROM oils;"
    oils = execute_read_query(select_query)
    print("Содержимое таблицы 'oils':", oils)
