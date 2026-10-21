#!/bin/bash
#SBATCH -p CPU-96C3TB
#SBATCH -N 1
#SBATCH -n 40
#SBATCH --qos=qos_cpu_96c3tb
#SBATCH --time=15-00
#SBATCH	--mem 360G

export PATH=$PATH:/home/feng2011group/lidongyang2023/software/openmpi411/bin
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/home/feng2011group/lidongyang2023/software/openmpi411/lib
export PATH=$PATH:/home/feng2011group/lidongyang2023/software/orca504
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/home/feng2011group/lidongyang2023/software/orca504
alias orca='/home/feng2011group/lidongyang2023/software/orca504/orca'

work_dir=`pwd`
echo $work_dir

icc=0
for i in `ls -F |grep /`
do
cd ./$i
echo -------$i---------
for inf in *.inp
do
((icc++))
if [ -f *.mkl ]
then 
  orca_2mkl ${inf%.*} -gbw
  echo forming .gbw file
fi
echo Running ${inf} ... \ $icc 
/home/feng2011group/lidongyang2023/software/orca504/orca ${inf%.*}.inp > ${inf%.*}.out      
echo ${inf} has finished
echo "${inf}" >> ../Energies.txt
grep 'FINAL SINGLE POINT ENERGY' ${inf%.*}.out >> ../Energies.txt
echo
done
cd $work_dir
done
