#Convert geometry (final, input orientation) in all Gaussian .out files in current folder to .gjf file by Multiwfn
#!/bin/bash
icc=0
nfile=`ls *.out|wc -l`
for inf in *.fchk
do
((icc++))
echo Converting ${inf} to ${inf//fchk/mkl} ... \($icc of $nfile\)
/home/feng2011group/lidongyang2023/software/Multiwfn_3.8_dev_bin_Linux_noGUI/Multiwfn ${inf} << EOF > /dev/null
100
2
9
${inf//fchk/mkl}
y
0
q
EOF
done
