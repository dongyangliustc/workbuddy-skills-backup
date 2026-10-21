#!/bin/bash
#SBATCH -p CPU-192C768GB
#SBATCH -N 1
#SBATCH -n 40
#SBATCH --qos=qos_cpu_192c768gb
#SBATCH	--mem 60G

module load gaussian/16/g16.c02

work_dir=`pwd`
echo --------------$work_dir-------------
echo 

for i in $(ls -F | grep /)
do
  cd ${i}

for file in *.gjf; do
    if [ -f "$file" ]; then
        echo "处理文件: $file"
       
        # 获取文件名（不带扩展名）
        base_name="${file%.gjf}"

unfchk ${base_name}.fchk

time g16 < ${base_name}.gjf > ${base_name}.out        #输出时间
echo ${file} has finished
formchk ${base_name}.chk
rm fort.7
    fi
done

cd ..
done

