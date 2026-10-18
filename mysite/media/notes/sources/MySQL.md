# **MySQL**



## 基础设定

### 启动与停止

启动： net start +用户名

停止： net stop +用户名



### 数据模型

![](C:\Users\ASUS\Pictures\Screenshots\屏幕截图 2025-11-02 142130.png)

注意：

1、DBMS是一个软件，用于创建、操纵、维护数据库

2、数据库中的数据存储在表结构中



### 关系型数据库

![](C:\Users\ASUS\Pictures\Screenshots\屏幕截图 2025-11-02 144503.png)







## SQL

### SQl通用语法

![](C:\Users\ASUS\Pictures\Screenshots\屏幕截图 2025-11-02 144751.png)





### SQL分类

![](C:\Users\ASUS\Pictures\Screenshots\屏幕截图 2025-11-02 145201.png)





### DDL

**DDL-数据库操作**

![](C:\Users\ASUS\Pictures\Screenshots\屏幕截图 2025-11-02 151525.png)



查询：

<mysql>show databases;



<mysql>select database();

如果要改变查询的数据库：<mysql>use+数据库名字；



创建：

<mysql>create database+数据库名字；

如果已存在再创建则会报错，解决方法：create database if not exists+用户名；



删除：

<mysql>drop database+数据库名字；

如果已存在再删除则会报错，解决方法：drop database if exists+用户名；







**DDL-表结构-查询**

查询当前数据库所有表：show tables；



查询表结构：desc+表名；



查询指定表的建表语句（表的详细信息）：show create table+表名；





**DDL-表操作-创建**

![](C:\Users\ASUS\Pictures\Screenshots\屏幕截图 2025-11-02 153636.png)





例子：

<mysql>create table tb_haha(

​           -->1号 int comment ‘年龄’，

​		   -->2号 varchar（50） comment ‘姓名’

​		   -->） comment ‘用户表’；



注意：

1、最后一段的comment‘用户表’后面什么都不加

2、在SQL语句中varchar代表字符串，（）内是字符串长度



 



**DDL-表操作-数据类型**

MySQl数据主要分为三类：数值类型、字符串类型、日期时间类型

<img src="C:\Users\ASUS\Pictures\Screenshots\屏幕截图 2025-11-02 154734.png" style="zoom:200%;" />

![](C:\Users\ASUS\Pictures\Screenshots\屏幕截图 2025-11-02 163358.png)







**DDL-表操作-修改**

添加字段：alter table 表名 add 字段名 类型（长度）comment ‘注释’；



修改数据类型：alter table 表名 modify 字段名 新数据类型（长度）；



修改字段名和字段类型：alter table 表名 change 旧字段名 新字段名 类型（长度）comment'注释'；



删除字段：alter table 表名 drop 字段名；



修改表名：alter table 表名 rename to 新表名；



删除表：drop table [if exists] 表名；



删除指定表，并重新创建该表：truncate table 表名；

注意：删除之后表中所有数据将会消失，重新创建只会留下表结构









### DML

**DML-添加数据**

给指定字段添加数据：insert into 表名（字段名1，字段名2，......）values（值1，值2，......);



给全部字段添加数据：insert into 表名 values（值1，值2，......);



批量添加数据：insert into 表名（字段名1，字段名2，......）values（值1，值2，......)，（值1，值2，......),(值1，值2，......);



![](C:\Users\ASUS\Pictures\Screenshots\屏幕截图 2025-11-09 172135.png)







**DML-修改数据**

update 表名 set 字段名1=值1，字段名2=值2，......[where 条件]；

注意：修改语句的条件可有可无，如果没有条件，会修改整张表的所有数据。





**DML-删除数据**

delete from 表名 [where 条件]；

注意：删除语句的条件可有可无，如果没有条件，会删除整张表的所有数据。









<u>以上为黑马教程部分，以下为菜鸟教程部分。</u>









## MySQL插入数据

格式：

INSERT INTO table_name (column1, column2, column3, ...)   VALUES   (value1, value2, value3, ...);

注意：数据是字符型，必须使用单引号 ' 或者双引号 "。





如果要插入所有列的数据，可以省略列名：

INSERT INTO users  VALUES  (NULL,'test', 'test@runoob.com', '1990-01-01', true);

注意：NULL 是用于自增长列的占位符，表示系统将为 id 列生成一个唯一的值。如上，id列将全部为test。





如果你要插入多行数据，可以在 VALUES 子句中指定多组数值：
INSERT INTO users (username, email, birthdate, is_active)
VALUES
    ('test1', 'test1@runoob.com', '1985-07-10', true),
    ('test2', 'test2@runoob.com', '1988-11-25', false),
    ('test3', 'test3@runoob.com', '1993-05-03', true);

username: 用户名，字符串类型。
email: 邮箱地址，字符串类型。
birthdate: 用户生日， 日期类型。
is_active: 是否已激活，布尔类型。







## MySQL查询数据

选择所有列的所有行:

select*from  users;



选择特定列的所有行:

select username,email from users;



选择满足条件的行:

select*from users <u>where</u>  is_active=true;



按照某列的升序排列(默认升序:asc):

select*from users <u>order by</u> birthdate;



按照某列的降序排列:

select*from users <u>order by</u> birthdate <u>desc</u>;



限制返回行数:

select*from users <u>limit 10</u>;



## WHERE子句

注意：在 WHERE 子句中，你可以使用各种条件运算符（如 =, <, >, <=, >=, !=），逻辑运算符（如 AND, OR, NOT），以及通配符（如 %）等。



使用 AND 运算符和通配符:
SELECT * FROM users WHERE username LIKE 'j%' AND is_active = TRUE;

使用 OR 运算符:
SELECT * FROM users WHERE is_active = TRUE OR birthdate < '1990-01-01';

使用 IN 子句:
SELECT * FROM users WHERE birthdate IN ('1990-01-01', '1992-03-15', '1993-05-03');



使用 BINARY 关键字来设定 WHERE 子句的字符串比较是区分大小写的:

mysql> SELECT * from runoob_tbl WHERE <u>BINARY</u> runoob_author='runoob.com';
Empty set (0.01 sec)



mysql> SELECT * from runoob_tbl WHERE <u>BINARY</u> runoob_author='RUNOOB.COM';



注意:因为binary区分大小写，所以runoob_author='runoob.com' 的查询条件是没有数据的。





## UPDATE更新

<u>UPDATE</u> table_name
<u>SET</u> column1 = value1, column2 = value2, ...WHERE condition;

注意:column是要更新的列的名称，value是新的值。如果忽略where子句，将更新表中所有行。







## DELETE语句

delete from table_name where condition;

注意:如果省略where子句将会删除表中所有行







## LIKE子句

select column1,column2,......from table_name where column_name like pattern;

用于在where子句中进行模糊匹配

如果没有使用%，like可以代替等号=

like通常与%一同使用



百分号通配符%:

select*from customers where last_name like 'a%';

以上SQL语句将选择所有姓氏以'a'开头的客户(字符串)

如果是'%com',将选择以'com'结尾的姓氏的客户





下划线通配符_:

select*from products where product_name like '_a%';

以上SQL语句将选择产品名称的第二个字符为'a'的产品





%和_组合使用:

select*from users where username like 'a%p_';

以上SQL语句将选择字母'a'开头，然后是多个或零个字符，接着是'p'字符，最后是任意字符的用户名。例如："aopo"、"apoo"





不区分大小写的匹配:

select*from employees where last_name like 'sim%'  COLLATE utf8mb4_general_ci;

以上SQL语句将选择姓氏以'sim'开头的所有员工，不区分大小写。







## UNION操作符

用于连接两个以上的select语句的结果组合到一个结果集合，并去除重复的行。

注意:必须由两个及以上的select语句组成，每个 select 语句的列数和对应位置的数据类型必须相同。



基本语句:

select city from customers

union

select city from suppliers

order by city;

以上SQL语句将选择客户表和供应商中所有城市的唯一值，并按城市名称升序排序。



与where子句组合:

select city from customers where gender='man'

union

select city from customers where gender='woman'

order by customers;

以上SQL语句将选择性别为男和女的客户，并按客户升序排序。



注意：union操作中的列数和数据类型必须相同



使用union all不去除重复行:

select city from customers

union all

select city from suppliers

order by city;

以上SQL语句使用union all将客户表和供应商表中的所有城市合并在一起，不去除重复行。



<u>更多实例见菜鸟教程MySQL UNION小节</u>





## ORDER BY(排序)语句

可以使用 ASC或 DESC 关键字来设置查询结果是按升序或降序排列。 默认情况下，它是按升序排序。



单列排序:

select*from products

order by product_name ASC;

以上SQL语句将选择产品表products中所有产品，并按产品名称升序排序。





多列排序:

select*from employees

order by department_id ASC,hire_date DESC;

以上SQL语句将选择员工表employees中的所有员工，先通过部门ID升序排列，再在相同部门中根据雇佣日期来降序排序。





使用数字表示列的位置:

select first_name,last_name,salary

from employees

order by 3 DESC,1 ASC;

以上SQL语句将选择员工表employees中的名字和工资列，先按照第3列salary降序排列，然后按照第1列first_name升序排列。





使用表达式排序:

select product_name,price*discount_rate AS discounted_price

from products

order by discounted_price DESC;

以上SQL语句将选择产品表中products的产品名称列和根据折扣率计算的折扣后的价格，并按照折扣后价格降序排列。





使用NULLS FIRST/LAST处理NULL值:

select product_name,price

from products

order by price DESC NULLS LAST;

以上SQL语句将选择产品表products中产品名称和价格，并按照价格降序排列，将NULL值排在最后。







## GROUP BY语法

SQL 查询中用于汇总和分析数据的重要工具，尤其在处理大量数据时，它能够提供有用的汇总信息。

可以使用COUNT, SUM, AVG,等函数。



例如

select customer_id,SUM(order_amount) AS total_amount

from orders

group by customer_id;

以上SQL语句中group by customer_id将结果按customer_id列分组，然后用SUM计算每个组中order_amount列的总和。

AS total_amount是为了给计算结果取个别名。





<u>使用WITH ROLLUP</u>

可以在分组统计数据的基础上再进行相同的统计（SUM、AVG、COUNT......）



例如

select name,SUM(signin) as signin_count from employee_tb1 group by name with rollup；

以上MySQL语句将对名字进行分组，再统计每个人登陆的次数。

其中NULL表示所有人的登录次数。

↓

+--------+--------------+
| name   | signin_count |
+--------+--------------+
| 小丽 |            2 |
| 小明 |            7 |
| 小王 |            7 |
| NULL   |           16 |
+--------+--------------+





<u>使用coalesce语句</u>

使用coalesce来设置一个可以取代NULL的名称



例如

select coalesce(name,'总数'),SUM(signin) as signin_count from employee_tb1 group by name with rollup;



参数说明：如果 a==null，则选择 b；如果 b==null,则选择 c；如果 a!=null,则选择 a；如果 a b c 都为 null ，则返回为 null（没意义）。
以下实例中如果名字为空我们使用总数代替：

↓

+--------------------------+--------------+
| coalesce(name, '总数') | signin_count |
+--------------------------+--------------+
| 小丽                   |            2 |
| 小明                   |            7 |
| 小王                   |            7 |
| 总数                   |           16 |
+--------------------------+--------------+







## MySQL连接

INNER JOIN(内连接，或等值连接):获取两个表中字段匹配关系的记录。

LEFT JOIN(左连接):获取左表所有记录，即使右表没有对应匹配的记录。

RIGHT JOIN(右连接):与 LEFT JOIN 相反，用于获取右表所有记录，即使左表没有对应匹配的记录。















## MySQL NULL值处理

在MySQL中，NULL用于表示缺失的或未知的数据

注意：处理NULL值需要特别小心，在数据库中它可能会导致不同于预期的结果。



三大运算符:

**IS NULL**: 当列的值是 NULL,此运算符返回 true。

**IS NOT NULL**: 当列的值不为 NULL, 运算符返回 true。

**<=>**: 比较操作符（不同于 = 运算符），当比较的的两个值相等或者都为 NULL 时返回 true。



注意:不能使用=NULL或!=NULL在列中查找NULL值

NULL值与其他任何值比较(包括NULL),返回的值永远都是NULL。





检查某列是否为NULL，可以使用IS NULL或IS NOT NULL条件。

select*from employees where department_id IS NULL/IS NOT NULL;



使用COALESCE函数处理NULL:

COALESCE函数可以用于替换为NULL的值，它接受多个参数，返回参数列表中的第一个非NULL值:

select product_name,coalesce(stock_quantity,0) AS actual_quantity from products;

以上SQL语句中，如果stock_quantity列为NULL，则COALESCE将返回0。





使用IFNULL函数处理NULL:

IFNULL 函数是 COALESCE 的 MySQL 特定版本，它接受两个参数，如果第一个参数为 NULL，则返回第二个参数。

select product_name,IFNULL(stock_quantity,0) AS actual_quantity from products;





NULL排序:

在使用order by子句进行排序时，NULL值默认会被放在排序的最后。如果想要将NULL值放在最前面，使用order by column_name ASC NULLS FIRST,反之则使用order by column_name DESC NULLS LAST。

select product_name,price

from products

order by price ASC NULLS FIRST;



使用<=>操作符进行NULL比较:

<=>操作符是MySQL中用于比较两个表达式是否相等的特殊操作符,对于NULL值的比较也会返回TRUE。它可以用于处理NULL值的等值比较。

select*from employees where commission<=>NULL;





注意聚合函数对NULL的处理:

在使用聚合函数(如COUNT,SUM,AVG)时，它们会忽略NULL值，可能会得到不同于预期的结果。如果希望将NULL视为0，可以使用COALESCE或IFNULL；

select AVG(COALESCE(salary,0)) AS avg_salary from employees;

以上SQL语句，即使salary为NULL，聚合函数也会将其视为0。









## MySQL正则表达式

除了LIKE...%模糊匹配

![](C:\Users\ASUS\Pictures\Screenshots\屏幕截图 2025-11-14 181638.png)





正则表达式匹配的字符类
.：匹配任意单个字符。
^：匹配字符串的开始。
$：匹配字符串的结束。
*：匹配零个或多个前面的元素。
+：匹配一个或多个前面的元素。
?：匹配零个或一个前面的元素。
[abc]：匹配字符集中的任意一个字符。
[^abc]：匹配除了字符集中的任意一个字符以外的字符。
[a-z]：匹配范围内的任意一个小写字母。
[0-9]：匹配一个数字字符。
\w：匹配一个字母数字字符（包括下划线）。
\s：匹配一个空白字符。





使用REGEXP进行模式匹配

REGEXP用于检查一个字符串是否匹配指定的正则表达式模式

基本语法:

select column1,column2,...

from table_name

where column_name REGEXP 'pattern';





以下是一些例子

查找name字段中以'st'为开头的所有数据:

select name from person_tb1 where name REGEXP '^st';



查找name字段中以'ok'为结尾的所有数据:

select name from person_tb1 where name REGEXP 'ok$';



查找name字段中包含'mar'字符串的所有数据:

select name from person_tb1 where name REGEXP 'mar';



查找name字段中以元音字符开头或以 'ok'字符串结尾的所有数据:

select name from person_tb1 where name REGEXP '^[aeiou]|ok$';



选择订单表中描述中包含"item"后跟一个或多个数字的记录。

select*from orders where order_description REGEXP 'item[0-9]+';



使用BINARY关键字，使得匹配区分大小写:

select*from products where product_name REGEXP BINARY 'apple';



使用OR进行多个匹配条件，以下将选择姓氏为"Smith"或"Johnson"的员工记录:

select*from employees where last_name REGEXP 'Smith|Johnson';





使用RLIKE进行模式匹配

RLIKE与REGEXP是一样的，可以互换使用，没有区别。

基本语法:

select column1,column2,...

from table_name

where column_name RLIKE 'pattern';









## MySQL事务

事务用来管理insert、update、delete语句

一般来说，事务必须满足4个条件:

![](C:\Users\ASUS\Pictures\Screenshots\屏幕截图 2025-11-14 194755.png)



注意:在 MySQL 命令行的默认设置下，事务都是**自动提交**的，即执行 SQL 语句后就会马上执行 COMMIT 操作。因此要显式地开启一个事务务须使用命令 BEGIN 或 START TRANSACTION，或者执行命令 SET AUTOCOMMIT=0，用来禁止使用当前会话的自动提交。





事务控制语句:

BEGIN 或 START TRANSACTION 显式地开启一个事务；



COMMIT 也可以使用 COMMIT WORK，不过二者是等价的。COMMIT 会提交事务，并使已对数据库进行的所有修改成为永久性的；



ROLLBACK 也可以使用 ROLLBACK WORK，不过二者是等价的。回滚会结束用户的事务，并撤销正在进行的所有未提交的修改；



SAVEPOINT identifier，SAVEPOINT 允许在事务中创建一个保存点，一个事务中可以有多个 SAVEPOINT；



RELEASE SAVEPOINT identifier 删除一个事务的保存点，当没有指定的保存点时，执行该语句会抛出一个异常；



ROLLBACK TO identifier 把事务回滚到标记点；



SET TRANSACTION 用来设置事务的隔离级别。InnoDB 存储引擎提供事务的隔离级别有READ UNCOMMITTED、READ COMMITTED、REPEATABLE READ 和 SERIALIZABLE。



MySQL事务处理的两种方法:

1、用BEGIN、ROLLBACK、COMMIT来实现

BEGIN或START TRANSACTION:用于开始一个事务。

ROLLBACK:事务回滚，取消之前的更改。

commit:事务确认，提交事务，使更改永久生效。



2、直接用SET来改变MySQL的自动提交模式:

SET AUTOCOMMIT=0 禁止自动提交

SET AUTOCOMMIT=1 开启自动提交









## MySQL ALTER命令

ALTER 命令允许你添加、修改或删除数据库对象，并且可以用于更改表的列定义、添加约束、创建和删除索引等操作。

ALTER 命令非常强大，可以在数据库结构发生变化时进行灵活的修改和调整。



添加列

ALTER TABLE table_name

ADD COLUMN new_column_name datatype;



例子

ALTER TABLE employees

ADD COLUMN birth_date DATE;

以上SQL语句在employees表中添加了一个名为birth_date的日期列







修改列的数据类型

ALTER TABLE TABLE NAME

MODIFY COLUMN column_name new_datatype;



例子

ALTER TABLE employees

MODIFY COLUMN salary DECIMAL(10,2);

以上SQL语句将employees表中的salary列的数据类型修改为DECIMAL(10,2)







修改列名

ALTER TABLE table_name

CHANGE COLUMN old_column_name new_column_name datatype;



例子

ALTER TABLE employees

CHANGE COLUMN old_column_name new_column_name VARCHAR(255);

以上SQL语句将employees表中的某列名字由old_column_name修改为new_column_name,并且可以同时修改数据类型







删除列

ALTER TABLE table_name

DROP COLUMN column_name;



例子

ALTER TABLE employees

DROP COLUMN birth_date;

以上SQL语句将employees表中的birth_date列删除







添加PRIMARY KEY

ALTER TABLE table_name

ADD PRIMARY KEY(column_name);



例子

ALTER TABLE employees

ADD PRIMARY KEY(employee_id);

以上SQL语句在employees表中添加了一个主键







添加FOREIGN KEY

ALTER TABLE child_table

ADD CONSTRAINT fk_name

FOREIGN KEY(column_name)

REFERENCES parent_table(column_name);



例子

ALTER TABLE orders

ADD <u>CONSTRAINT fk_customer//外键约束</u>

FOREIGN KEY(customer_id)

REFERENCES customers(customer_id);

以上SQL语句在orders表中添加了一个外键，关联到customers表的customer_id列







修改表名

ALTER TABLE old_table_name

RENAME TO new_table_name;



例子

ALTER TABLE employees

RENAME TO staff;

以上SQL语句将表名由employees修改为staff







## MySQL索引

MySQL 索引是一种数据结构，用于加快数据库查询的速度和性能。
MySQL 索引的建立对于 MySQL 的高效运行是很重要的，索引可以大大提高 MySQL 的检索速度。



